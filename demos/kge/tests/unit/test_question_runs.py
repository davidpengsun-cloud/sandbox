"""Background warehouse and shared-run contracts, with fixed BigQuery job budgets."""
import asyncio
import datetime as dt
from decimal import Decimal
import json
import threading
import time
from types import SimpleNamespace

import pytest

from kge.adapters.questionstate import for_profile
from kge.adapters.warehouse.duckdb import DuckDBEstate, Warehouses
from kge.adapters.warehouse.bigquery import BigQueryWarehouses
from kge.adapters.warehouse import jobs
from kge.adapters.bigquery import BigQueryLoader, QueryFailed
from kge.pipeline import questionrun
from kge.pipeline.sqlguard import Allowlist
from kge.ports import QuestionRuns, StateConflict, WarehouseSet
from kge.services.ask import service
from kge.services.ask.engine.config import PRESETS
from kge.services.ask.models import Ask
from kge.services.ask.routes import _run_response
from tests import bigquery_fake
from tests.unit.test_planner_jobs import jobs as count_jobs


def native(monkeypatch):
    fake = bigquery_fake.install(monkeypatch)
    get = fake.get
    def read(url):
        data = get(url)
        if '/jobs/' in url and not url.endswith('/cancel'):
            data['status'] = data.get('status') or {'state': 'DONE' if data.get('jobComplete') else 'RUNNING'}
            data['statistics'] = {'creationTime': '1000', 'startTime': '1000', 'endTime': '402000',
                                  'totalSlotMs': '12345', 'query': {'totalBytesProcessed': '678'}}
        return data
    monkeypatch.setattr(fake, 'get', read)
    return fake


@pytest.fixture(params=['duckdb', 'bigquery'])
def warehouse(request, tmp_path, monkeypatch):
    fake = None
    if request.param == 'bigquery':
        fake = native(monkeypatch)
        warehouse = BigQueryWarehouses(tmp_path, ['u'], project=bigquery_fake.PROJECT, client='sample')
        table = f'`{bigquery_fake.PROJECT}.{warehouse.dataset_id}.u_events`'
        fake.run(None, f'CREATE TABLE {table} (value INT64)')
        fake.run(None, f'INSERT INTO {table} VALUES (7), (8)')
        fake.requests.clear()
    else:
        estate = DuckDBEstate(tmp_path)
        with estate.stage('u', ['CREATE TABLE events(value INTEGER)']) as session:
            session.execute('INSERT INTO events VALUES (7), (8)')
        estate.publish('u')
        warehouse = Warehouses(tmp_path, ['u'])
    yield warehouse, fake
    warehouse.close()
    if fake:
        fake.con.close()


def statement(sql='SELECT SUM(value) FROM events'):
    return {'unit': 'u', 'units': ['u'], 'sql': sql,
            'allow': Allowlist('u', {'events'}, {'events': {'value'}}, schema={'events': {'value': 'INTEGER'}})}


def complete(warehouse, handle):
    for _ in range(100):
        result = warehouse.poll(handle)
        if result['state'] != 'running':
            return result
        time.sleep(.001)
    pytest.fail('local worker did not complete')


def test_shared_warehouse_submit_poll_contract(warehouse):
    w, fake = warehouse
    assert isinstance(w, WarehouseSet)
    ready = []
    handle = w.submit(statement(), job_id='ask_contract', on_ready=lambda *args: ready.append(args))
    assert handle['guard']['passed'] and len(ready) == 1
    result = complete(w, handle)
    assert result['state'] == 'done' and result['rows'] == [(15,)]
    assert {'elapsed_ms', 'bytes_processed', 'slot_ms'} <= result['progress'].keys()
    repeated = w.poll(handle)
    assert repeated['rows'] == [(15,)]
    assert repeated['progress'] == result['progress']
    w.release(handle)
    if fake:
        assert count_jobs(fake) == (1, 1)
        assert result['progress'] == {'elapsed_ms': 401000, 'bytes_processed': 678, 'slot_ms': 12345}
        assert handle['job']['reference']['location'] == 'US'


def test_gate_refusal_submits_no_statement(warehouse):
    w, fake = warehouse
    handle = w.submit(statement('SELECT private FROM events'), job_id='ask_refused')
    assert not handle['guard']['passed']
    assert w.poll(handle) == {'state': 'done', 'rows': None}
    if fake:
        assert count_jobs(fake) == (0, 0)


@pytest.fixture(params=['store', 'gcs', 'bigquery'])
def states(request, tmp_path, monkeypatch):
    monkeypatch.setenv('QUESTION_STATE_BACKEND', 'bigquery' if request.param == 'bigquery' else 'store')
    monkeypatch.setenv('STORE_URL', str(tmp_path / 'store'))
    monkeypatch.setenv('BIGQUERY_PROJECT', bigquery_fake.PROJECT)
    if request.param == 'gcs':
        from kge.adapters.objstore.gcs import GcsStore
        from urllib.parse import parse_qs, unquote, urlsplit
        monkeypatch.setenv('STORE_URL', 'gs://question-test/shared')
        objects, generations, lock = {}, {}, threading.Lock()
        def call(self, method, url, key, data=None, json_body=False):
            parsed = urlsplit(url)
            query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            name = (self.bucket, query.get('name') or unquote(parsed.path.rsplit('/', 1)[-1]))
            with lock:
                current, content = objects.get(name, (0, None))
                if method == 'GET':
                    if content is None:
                        return 404, b''
                    if query.get('alt') == 'media':
                        return 200, generations[(name, query['generation'])]
                    return 200, json.dumps({'generation': str(current)}).encode()
                assert method == 'POST'
                if query['ifGenerationMatch'] != str(current):
                    return 412, b''
                objects[name] = current + 1, data
                generations[(name, str(current + 1))] = data
                return 200, json.dumps({'generation': str(current + 1)}).encode()
        monkeypatch.setattr(GcsStore, 'call', call)
    fake = bigquery_fake.FakeBigQuery()
    from kge.adapters.appstate import bigquery
    from kge.adapters.questionstate import bigquery as runstate
    monkeypatch.setattr(runstate, '_prepared', set())
    def post(self, body):
        fake.requests.append({'body': body})
        return fake.state_post(body)
    monkeypatch.setattr(bigquery.BigQueryState, '_post', post)
    monkeypatch.setattr(bigquery.BigQueryState, '_project', lambda self: bigquery_fake.PROJECT)
    def new(client='sample'):
        return for_profile(SimpleNamespace(client=client, run_dir=lambda: tmp_path / client))
    yield new, fake
    fake.con.close()


def test_shared_question_state_cas_types_and_client_isolation(states):
    new, _ = states
    first, second = new(), new()
    assert isinstance(first, QuestionRuns)
    run_id = 'a' * 32
    assert first.get(run_id)[0] is None
    record = {'id': run_id, 'plan': {}, 'value': Decimal('1.20'), 'day': dt.date(2026, 1, 1)}
    revision = first.put(run_id, record, None)
    assert second.get(run_id) == (record, revision)
    with pytest.raises(StateConflict):
        second.put(run_id, {'id': run_id}, None)
    second.put(run_id, {**record, 'state': 'done'}, revision)
    with pytest.raises(StateConflict):
        first.put(run_id, record, revision)
    assert new('other').get(run_id)[0] is None


def run_setup(monkeypatch, w):
    rights = {'u'}
    c = SimpleNamespace(e=SimpleNamespace(w=w, identity='release-1'), g=SimpleNamespace(readable_units=lambda who: set(rights)))
    def compute(c, profile, question, who, purpose, arm, cfg, **kwargs):
        guard, rows, meta = questionrun.execute(w, statement(), 'reference', ())
        return {'question': question, 'principal': who, 'purpose': purpose, 'arm': arm, 'behaviour': 'figure',
                'figure': rows[0][0], 'answer_text': str(rows[0][0]), 'phrased_by': 'template',
                'trace': [], 'versions': {}, 'combined': False, 'run_trace': {}}
    monkeypatch.setattr(service, 'ask', compute)
    return c, rights


def test_run_reconnect_many_questions_no_waiters_and_constant_jobs(monkeypatch, tmp_path):
    fake = native(monkeypatch)
    try:
        w = BigQueryWarehouses(tmp_path, ['u'], project=bigquery_fake.PROJECT, client='sample')
        fake.run(None, f'CREATE TABLE `{bigquery_fake.PROJECT}.{w.dataset_id}.u_events` (value INT64)')
        fake.run(None, f'INSERT INTO `{bigquery_fake.PROJECT}.{w.dataset_id}.u_events` VALUES (15)')
        fake.requests.clear()
        fake.pending_polls = -1
        monkeypatch.setenv('STORE_URL', str(tmp_path / 'store'))
        monkeypatch.setenv('QUESTION_STATE_BACKEND', 'store')
        profile = SimpleNamespace(client='sample')
        c, rights = run_setup(monkeypatch, w)
        now = [time.time()]
        threads = set(threading.enumerate())
        runs = [service.start_run(c, profile, Ask(question='Metric'), 'analyst', PRESETS['graph'], clock=lambda: now[0]) for _ in range(64)]
        assert all(r['state'] == 'running' for r in runs)
        assert count_jobs(fake) == (64, 64)
        assert set(threading.enumerate()) <= threads
        assert count_jobs(fake) == (64, 64)
        assert len(fake.jobs) == 64
        state = for_profile(profile)
        run_id = runs[0]['id']
        assert service.get_run(c, profile, run_id, 'other', clock=lambda: now[0]) is None
        before = len(fake.requests)
        service.get_run(c, profile, run_id, 'analyst', clock=lambda: now[0])
        assert len(fake.requests) == before
        now[0] += 301
        pending = service.get_run(c, profile, run_id, 'analyst', clock=lambda: now[0])
        assert pending['state'] == 'running'
        assert count_jobs(fake) == (64, 64)
        handle = state.get(run_id)[0]['jobs'][0]['handle']
        fake.jobs[handle['job']['reference']['jobId']]['pending'] = 0
        now[0] += 3
        other = SimpleNamespace(e=SimpleNamespace(w=BigQueryWarehouses(tmp_path, ['u'], project=bigquery_fake.PROJECT, client='sample'), identity='release-1'), g=c.g)
        answer = service.get_run(other, profile, run_id, 'analyst', clock=lambda: now[0])
        assert answer['state'] == 'done' and answer['answer']['figure'] == 15
        assert answer['answer']['run_trace']['warehouse_jobs'] == [{'statement': 1, 'job_id': handle['id'],
                 'elapsed_ms': 401000, 'bytes_processed': 678, 'slot_ms': 12345}]
        assert count_jobs(fake) == (64, 64)
        before = len(fake.requests)
        assert service.get_run(c, profile, run_id, 'analyst', clock=lambda: now[0])['answer'] == answer['answer']
        assert len(fake.requests) == before
        rights.clear()
        denied = service.get_run(c, profile, run_id, 'analyst', clock=lambda: now[0])
        assert denied['state'] == 'failed' and not denied['answer'] and not denied['events']
    finally:
        fake.con.close()


def test_a_refused_statement_is_not_reported_as_a_warehouse_job(warehouse, monkeypatch, tmp_path):
    w, fake = warehouse
    monkeypatch.setenv('STORE_URL', str(tmp_path / 'runs'))
    monkeypatch.setenv('QUESTION_STATE_BACKEND', 'store')
    c, _ = run_setup(monkeypatch, w)
    def compute(*args, **kwargs):
        guard, rows, _ = questionrun.execute(w, statement('SELECT private FROM events'), 'reference', ())
        assert not guard['passed'] and rows is None
        return {'behaviour': 'abstain', 'run_trace': {}}
    monkeypatch.setattr(service, 'ask', compute)
    profile, now = SimpleNamespace(client='sample'), [time.time()]
    run = service.start_run(c, profile, Ask(question='Metric'), 'analyst', PRESETS['graph'], clock=lambda: now[0])
    now[0] += 3
    run = service.get_run(c, profile, run['id'], 'analyst', clock=lambda: now[0])
    assert run['state'] == 'done' and run['answer']['behaviour'] == 'abstain'
    assert run['answer']['run_trace']['warehouse_jobs'] == []
    if fake:
        assert count_jobs(fake) == (0, 0)


def test_run_timeout_is_clear_and_cancels_warehouse(monkeypatch, tmp_path):
    class Waiting:
        def submit(self, *args, **kwargs):
            return {'id': 'warehouse-job', 'guard': {'passed': True}, 'meta': {}, 'job': {'id': 'waiting'}}
        def poll(self, handle):
            pytest.fail('expired run must not poll')
        def cancel(self, handle):
            cancelled.append(handle['id'])
    cancelled = []
    monkeypatch.setenv('QUESTION_RUN_TIMEOUT', '5')
    monkeypatch.setenv('STORE_URL', str(tmp_path / 'store'))
    monkeypatch.setenv('QUESTION_STATE_BACKEND', 'store')
    c, _ = run_setup(monkeypatch, Waiting())
    profile = SimpleNamespace(client='sample')
    now = [1000.]
    monkeypatch.setattr(time, 'time', lambda: now[0])
    run = service.start_run(c, profile, Ask(question='Metric'), 'analyst', PRESETS['graph'], clock=lambda: now[0])
    service.get_run(c, profile, run['id'], 'analyst', clock=lambda: now[0])
    now[0] += 6
    expired = service.get_run(c, profile, run['id'], 'analyst', clock=lambda: now[0])
    assert expired['state'] == 'expired' and 'time limit' in expired['error']
    assert cancelled == ['warehouse-job']


def test_run_stream_reconnect_returns_id_and_saved_answer():
    answer = {'question': 'Metric', 'principal': 'analyst', 'purpose': 'analysis', 'arm': 'graph', 'behaviour': 'figure',
              'answer_text': '15', 'phrased_by': 'template', 'versions': {}, 'combined': False}
    out = {'id': 'a' * 32, 'state': 'done', 'events': [], 'answer': answer, 'cursor': 0,
           'expires_at': time.time() + 900, 'poll_after': 2}
    response = _run_response(SimpleNamespace(headers={'accept': 'application/x-ndjson'}), out)
    async def collect():
        return [json.loads(line) async for line in response.body_iterator]
    events = asyncio.run(collect())
    assert events[0]['event'] == 'run' and events[0]['id'] == out['id']
    assert events[1] == {'event': 'answer', 'answer': out['answer']}


def test_native_submit_retries_same_id_and_keeps_execution_project(monkeypatch):
    calls = []
    loader = BigQueryLoader(project='billing-project', sleeper=lambda _: None)
    monkeypatch.setattr(loader, 'token', lambda: ('offline', 'billing-project'))
    monkeypatch.setattr(loader, 'get', lambda url, headers: {'location': 'EU'})
    def post(url, headers, body):
        calls.append((url, body))
        if len(calls) == 1:
            raise QueryFailed('busy', reason='backendError', code=503)
        return {'jobReference': body['jobReference']}
    monkeypatch.setattr(loader, 'post', post)
    handle = jobs.submit(loader, 'SELECT 1', ('data-project', 'dataset'), 'ask_fixed', 1000)
    assert len(calls) == 2 and calls[0] == calls[1]
    body = calls[0][1]
    assert handle['reference'] == {'projectId': 'billing-project', 'jobId': 'ask_fixed', 'location': 'EU'}
    assert body['configuration']['query']['defaultDataset'] == {'projectId': 'data-project', 'datasetId': 'dataset'}
    assert body['configuration']['query']['maximumBytesBilled'] == '1000'
    assert body['configuration']['jobTimeoutMs'] == '600000'


@pytest.mark.parametrize('name', ['QUESTION_RUN_TIMEOUT', 'QUESTION_POLL_SECONDS'])
@pytest.mark.parametrize('value', ['0', '-1', 'nan', 'inf', 'bad', '1200'])
def test_question_limits_refuse_bad_settings(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        questionrun.timeout() if name == 'QUESTION_RUN_TIMEOUT' else questionrun.interval()


def test_async_external_runtime_keeps_renaming_gates_and_job_budget(monkeypatch):
    from tests.unit.test_runtime_bigquery_projects import runtime, ProjectRest, DATA, DATASET, BILLING, RESULT
    from tests.unit.test_bigquery import JsonResponse
    from urllib.parse import urlsplit
    from kge.pipeline import trace
    from kge.services.ask.engine.runtrace import start
    rest = ProjectRest(DATA)
    def open_http(request, timeout):
        path = urlsplit(request.full_url).path
        if request.method == 'GET' and '/jobs/' in path:
            rest.calls.append(('GET', path, None))
            return JsonResponse({'status': {'state': 'DONE'}, 'statistics': {'startTime': '1000', 'endTime': '402000',
                'totalSlotMs': '55', 'query': {'totalBytesProcessed': '10'}}})
        return rest.open(request, timeout)
    monkeypatch.setattr('kge.adapters.http.open_http', open_http)
    monkeypatch.setattr(BigQueryLoader, 'token', lambda self: ('offline', BILLING))
    manager, allow = runtime(f'{DATA}.{DATASET}')
    guard, job, meta = manager.execute_statement({'unit': 'u', 'sql': 'SELECT SUM(amount) FROM flow', 'allow': allow},
                                                 runtime_id='live', job_id='ask_runtime')
    assert guard['passed'] and meta['physical_tables'] == ['FLOW']
    assert len(rest.jobs) == 2
    assert rest.jobs[0]['dryRun'] and rest.jobs[1]['jobReference']['jobId'] == 'ask_runtime'
    assert 'net_amount' in rest.jobs[1]['configuration']['query']['query']
    result = manager.poll({'runtime_id': 'live', 'job': job})
    assert result['state'] == 'done' and result['rows'] == [(250,)]
    assert result['progress'] == {'elapsed_ms': 401000, 'bytes_processed': 10, 'slot_ms': 55}
    before = len(rest.calls)
    manager.release({'runtime_id': 'live', 'job': job})
    assert len(rest.calls) == before
    assert len(rest.jobs) == 2
    with start('ask') as recorded:
        jobs.record_completed(manager._bigquery_loader('live')[0], {**RESULT, 'jobReference': job['reference']})
        assert recorded.finish({})['warehouse_jobs'] == [{'job_id': 'ask_runtime', **result['progress']}]
    assert trace.current() is None


def test_named_submission_recovers_a_lost_response_without_a_second_job(monkeypatch):
    loader = BigQueryLoader(project='billing-project')
    calls = []
    monkeypatch.setattr(loader, 'token', lambda: ('offline', 'billing-project'))
    def get(url, headers):
        calls.append(url)
        return ({'location': 'EU'} if '/datasets/' in url else {'jobReference': {'jobId': 'ask_same'},
                'configuration': {'query': {'query': 'SELECT 1', 'useLegacySql': False,
                    'defaultDataset': {'projectId': 'billing-project', 'datasetId': 'data'}}}})
    monkeypatch.setattr(loader, 'get', get)
    def duplicate(*args):
        raise QueryFailed('Already exists', code=409)
    monkeypatch.setattr(loader, 'post', duplicate)
    result = jobs.submit(loader, 'SELECT 1', 'data', 'ask_same')
    assert result['reference']['jobId'] == 'ask_same'
    assert len(calls) == 2 and '/jobs/ask_same?location=EU' in calls[-1]


def test_default_run_records_are_outside_pack_and_release_paths(tmp_path, monkeypatch):
    monkeypatch.setenv('STORE_URL', str(tmp_path))
    monkeypatch.setenv('QUESTION_STATE_BACKEND', 'store')
    state = for_profile(SimpleNamespace(client='sample'))
    state.put('a' * 32, {'id': 'a' * 32}, None)
    assert (tmp_path / 'questions' / 'sample' / 'runs' / ('a' * 32 + '.json')).is_file()
    assert not (tmp_path / 'clients').exists() and not (tmp_path / 'releases').exists()


def test_question_state_can_start_without_a_prior_read(states):
    new, fake = states
    state = new()
    run_id, record = 'b' * 32, {'plan': {}, 'state': 'running'}
    revision = state.put(run_id, record, None)
    assert new().get(run_id) == (record, revision)
    if fake.requests:
        assert len(fake.requests) == 3
        assert sum('CREATE TABLE' in r['body']['query'] for r in fake.requests) == 1
        fake.requests.clear()
        _, revision = new().get(run_id)
        new().put(run_id, {**record, 'state': 'done'}, revision)
        assert len(fake.requests) == 2
        assert all('CREATE TABLE' not in r['body']['query'] for r in fake.requests)


def test_question_request_job_budgets_include_optional_bigquery_state(states, monkeypatch):
    new, fake = states
    submitted, polls = [], []
    class Warehouse:
        def submit(self, *args, **kwargs):
            submitted.append(kwargs['job_id'])
            return {'id': kwargs['job_id'], 'guard': {'passed': True}, 'meta': {}, 'job': {}}
        def poll(self, handle):
            polls.append(handle['id'])
            return {'state': 'running'} if len(polls) == 1 else {'state': 'done', 'rows': [(15,)]}
        def release(self, handle):
            pass
    c, _ = run_setup(monkeypatch, Warehouse())
    state, profile, now = new(), SimpleNamespace(client='sample'), [time.time()]
    run = service.start_run(c, profile, Ask(question='Metric'), 'analyst', PRESETS['graph'], state=state, clock=lambda: now[0])
    bigquery_state = bool(fake.requests)
    assert len(fake.requests) == (5 if bigquery_state else 0)
    for expected_state, expected_jobs in [('running', 3), ('done', 4), ('done', 1)]:
        fake.requests.clear()
        now[0] += 3
        run = service.get_run(c, profile, run['id'], 'analyst', state=new(), clock=lambda: now[0])
        assert run['state'] == expected_state
        assert len(fake.requests) == (expected_jobs if bigquery_state else 0)
    assert len(submitted) == 1 and len(polls) == 2
    assert run['answer']['figure'] == 15


def test_question_state_callers_use_only_the_port_and_factory(monkeypatch):
    from tests.unit.test_layers import backend_imports
    assert backend_imports('questionstate', ('store', 'bigquery')) == []
    monkeypatch.setenv('QUESTION_STATE_BACKEND', 'other')
    with pytest.raises(ValueError, match='QUESTION_STATE_BACKEND'):
        for_profile(SimpleNamespace(client='sample'))


def test_two_instances_share_one_resume_lease_without_duplicate_submission(monkeypatch, tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls, polls = [], []
    class Warehouse:
        def submit(self, *args, **kwargs):
            calls.append(kwargs['job_id'])
            return {'id': kwargs['job_id'], 'guard': {'passed': True}, 'meta': {}, 'job': {'id': 'held'}}
        def poll(self, handle):
            polls.append(handle['id'])
            entered.set()
            assert release.wait(5)
            return {'state': 'running'}
    monkeypatch.setenv('MODEL_REQUEST_TIMEOUT', '299')
    monkeypatch.setenv('STORE_URL', str(tmp_path / 'store'))
    monkeypatch.setenv('QUESTION_STATE_BACKEND', 'store')
    c, _ = run_setup(monkeypatch, Warehouse())
    profile, now = SimpleNamespace(client='sample'), [time.time()]
    run = service.start_run(c, profile, Ask(question='Metric'), 'analyst', PRESETS['graph'], clock=lambda: now[0])
    now[0] += 3
    completed = []
    thread = threading.Thread(target=lambda: completed.append(service.get_run(c, profile, run['id'], 'analyst', clock=lambda: now[0])))
    thread.start()
    try:
        assert entered.wait(5)
        other = service.get_run(c, profile, run['id'], 'analyst', clock=lambda: now[0])
        assert other['state'] == 'running' and len(calls) == len(polls) == 1
        assert for_profile(profile).get(run['id'])[0]['lease'] == now[0] + 309
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and len(completed) == 1
    assert len(for_profile(profile).get(run['id'])[0]['jobs']) == 1
    assert len(calls) == len(polls) == 1


def test_each_warehouse_job_is_capped_by_the_runs_remaining_lifetime(monkeypatch):
    loader = BigQueryLoader(project='billing-project')
    sent = []
    monkeypatch.setattr(loader, 'token', lambda: ('offline', 'billing-project'))
    monkeypatch.setattr(loader, 'get', lambda *args: {'location': 'EU'})
    monkeypatch.setattr(loader, 'post', lambda url, headers, body: sent.append(body) or {'jobReference': body['jobReference']})
    with questionrun.checkpoints({'expires_at': time.time() + 30}, lambda: None):
        jobs.submit(loader, 'SELECT 1', 'data', 'ask_remaining')
    assert len(sent) == 1
    assert 0 < int(sent[0]['configuration']['jobTimeoutMs']) <= 30000


def test_expired_assembly_cannot_publish_a_late_answer(monkeypatch, tmp_path):
    from kge.adapters import deadline
    clock, monotonic = [1000.], [200.]
    monkeypatch.setattr(time, 'time', lambda: clock[0])
    monkeypatch.setenv('QUESTION_RUN_TIMEOUT', '10')
    monkeypatch.setenv('STORE_URL', str(tmp_path / 'store'))
    monkeypatch.setenv('QUESTION_STATE_BACKEND', 'store')
    c, _ = run_setup(monkeypatch, object())
    def assemble(*args, **kwargs):
        clock[0] += 11
        monotonic[0] += 11
        return {'question': 'Metric', 'answer_text': '15', 'run_trace': {}}
    monkeypatch.setattr(service, 'ask', assemble)
    monkeypatch.setattr(deadline.time, 'monotonic', lambda: monotonic[0])
    profile = SimpleNamespace(client='sample')
    run = service.start_run(c, profile, Ask(question='Metric'), 'analyst', PRESETS['graph'], clock=lambda: clock[0], presentation=True)
    ended = service.get_run(c, profile, run['id'], 'analyst', clock=lambda: clock[0])
    assert ended['state'] == 'expired' and 'time limit' in ended['error']
    assert 'answer' not in ended
    assert not any(event['event'] == 'answer' for event in ended['events'])


def test_distinct_planning_attempts_survive_replay_without_repeating_models():
    record, calls = {}, []
    def propose(value):
        calls.append(value)
        return {'plan': value}
    with questionrun.checkpoints(record, lambda: None):
        assert questionrun.memo('same-turn', lambda: propose('preferred')) == {'plan': 'preferred'}
        assert questionrun.memo('same-turn', lambda: propose('fallback')) == {'plan': 'fallback'}
    with questionrun.checkpoints(record, lambda: None):
        assert questionrun.memo('same-turn', lambda: pytest.fail('replayed preferred planning')) == {'plan': 'preferred'}
        assert questionrun.memo('same-turn', lambda: pytest.fail('replayed fallback planning')) == {'plan': 'fallback'}
    assert calls == ['preferred', 'fallback']


def test_reconnect_keeps_retrieval_and_governance_trace_from_earlier_phases(monkeypatch, tmp_path):
    class Warehouse:
        def submit(self, *args, **kwargs):
            return {'id': kwargs['job_id'], 'guard': {'passed': True}, 'meta': {}, 'job': {}}
        def poll(self, handle):
            return {'state': 'done', 'rows': [(15,)]}
        def release(self, handle):
            pass
    monkeypatch.setenv('STORE_URL', str(tmp_path / 'store'))
    monkeypatch.setenv('QUESTION_STATE_BACKEND', 'store')
    c, _ = run_setup(monkeypatch, Warehouse())
    compute = service.ask
    part = {'started_at': '2026-11-12T00:00:00+00:00',
            'retrieved': [{'doc': 'definition', 'chunk': 'definition:1', 'via': 'definition'}],
            'plan_rewrites': [{'rule': 'grounded', 'before': 'a', 'after': 'b'}],
            'data_reads': [{'unit': 'u', 'kind': 'query', 'row_count': 1}]}
    def traced(*args, **kwargs):
        questionrun.remember_trace(part)
        return compute(*args, **kwargs)
    monkeypatch.setattr(service, 'ask', traced)
    profile, now = SimpleNamespace(client='sample'), [time.time()]
    run = service.start_run(c, profile, Ask(question='Metric'), 'analyst', PRESETS['graph'], clock=lambda: now[0])
    now[0] += 3
    run = service.get_run(c, profile, run['id'], 'analyst', clock=lambda: now[0])
    assert run['state'] == 'done'
    for key, value in part.items():
        assert run['answer']['run_trace'][key] == value


def test_local_cancel_interrupts_a_running_worker():
    from kge.adapters.warehouse import localjobs
    import uuid
    started, interrupted, exited = threading.Event(), threading.Event(), threading.Event()
    def compute(register_cancel):
        register_cancel(interrupted.set)
        started.set()
        try:
            assert interrupted.wait(5)
            return [(15,)]
        finally:
            exited.set()
    handle = localjobs.submit('cancel_' + uuid.uuid4().hex, compute)
    assert started.wait(5)
    localjobs.cancel(handle)
    localjobs.cancel(handle)
    assert interrupted.wait(5) and exited.wait(5)


def test_resume_recovers_submission_with_the_same_unpredictable_job_id(monkeypatch, tmp_path):
    calls = []
    class Warehouse:
        def submit(self, *args, **kwargs):
            calls.append(kwargs['job_id'])
            if len(calls) == 1:
                raise questionrun.RunPending()
            return {'id': kwargs['job_id'], 'guard': {'passed': True}, 'meta': {}, 'job': {}}
        def poll(self, handle):
            return {'state': 'done', 'rows': [(15,)]}
        def release(self, handle):
            pass
    monkeypatch.setenv('STORE_URL', str(tmp_path / 'store'))
    monkeypatch.setenv('QUESTION_STATE_BACKEND', 'store')
    c, _ = run_setup(monkeypatch, Warehouse())
    profile, now = SimpleNamespace(client='sample'), [time.time()]
    run = service.start_run(c, profile, Ask(question='Metric'), 'analyst', PRESETS['graph'], clock=lambda: now[0])
    assert run['state'] == 'running' and run['id'] not in calls[0]
    for _ in range(2):
        now[0] += 3
        run = service.get_run(c, profile, run['id'], 'analyst', clock=lambda: now[0])
    assert run['state'] == 'done' and run['answer']['figure'] == 15
    assert len(calls) == 2 and calls[0] == calls[1]


def test_a_conflicting_job_cannot_supply_ungated_results(monkeypatch):
    from kge.adapters.deadline import ExternalCallError
    loader = BigQueryLoader(project='billing-project')
    monkeypatch.setattr(loader, 'token', lambda: ('offline', 'billing-project'))
    def get(url, headers):
        if '/datasets/' in url:
            return {'location': 'EU'}
        return {'configuration': {'query': {'query': 'SELECT 999',
            'defaultDataset': {'projectId': 'billing-project', 'datasetId': 'data'}}}}
    def duplicate(*args):
        raise QueryFailed('Already exists', code=409)
    monkeypatch.setattr(loader, 'get', get)
    monkeypatch.setattr(loader, 'post', duplicate)
    with pytest.raises(ExternalCallError, match='could not be verified'):
        jobs.submit(loader, 'SELECT 1', 'data', 'ask_existing')


@pytest.mark.parametrize('name,value', [('QUESTION_RUN_TIMEOUT', '1500'), ('QUESTION_POLL_SECONDS', '0'),
                                        ('QUESTION_START_WAIT', '60'), ('QUESTION_START_WAIT', '-1'),
                                        ('QUESTION_START_WAIT', 'soon')])
def test_out_of_range_question_settings_refuse_startup(monkeypatch, name, value):
    import os
    import subprocess
    import sys
    from kge.pipeline.settings import ROOT, check_service_auth_settings
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        check_service_auth_settings()
    result = subprocess.run([sys.executable, '-c', 'import kge.app'], cwd=ROOT,
                            env={**os.environ, 'MODEL_PROVIDER': 'none', 'PROFILE': '', 'PROFILES': ''},
                            capture_output=True, text=True, timeout=60)
    assert result.returncode != 0 and name in result.stderr


def test_a_short_question_answers_within_the_start_wait_without_a_poll_interval(warehouse, monkeypatch, tmp_path):
    w, _ = warehouse
    monkeypatch.setenv('STORE_URL', str(tmp_path / 'runs'))
    monkeypatch.setenv('QUESTION_STATE_BACKEND', 'store')
    monkeypatch.setenv('QUESTION_POLL_SECONDS', '30')
    c, _ = run_setup(monkeypatch, w)
    profile = SimpleNamespace(client='sample')
    began = time.monotonic()
    run = service.start_run(c, profile, Ask(question='Metric'), 'analyst', PRESETS['graph'], wait=10)
    assert time.monotonic() - began < 5
    assert run['state'] == 'done' and run['answer']['figure'] == 15
    held = service.start_run(c, profile, Ask(question='Metric'), 'analyst', PRESETS['graph'])
    assert held['state'] == 'running' and 'answer' not in held


def test_a_long_question_falls_back_to_the_background_after_the_start_wait(monkeypatch, tmp_path):
    class Running:
        def submit(self, *args, **kwargs):
            return {'id': kwargs['job_id'], 'guard': {'passed': True}, 'meta': {}, 'job': {}}
        def poll(self, handle):
            polls.append(time.monotonic())
            return {'state': 'running'}
    polls = []
    monkeypatch.setenv('STORE_URL', str(tmp_path / 'store'))
    monkeypatch.setenv('QUESTION_STATE_BACKEND', 'store')
    c, _ = run_setup(monkeypatch, Running())
    began = time.monotonic()
    run = service.start_run(c, SimpleNamespace(client='sample'), Ask(question='Metric'), 'analyst', PRESETS['graph'], wait=.3)
    assert run['state'] == 'running' and 'answer' not in run
    assert .3 <= time.monotonic() - began < 3 and len(polls) > 1


@pytest.mark.parametrize('stage', ['assembly', 'warehouse'])
def test_an_over_limit_run_reports_one_outcome_on_start_and_on_every_poll(monkeypatch, tmp_path, stage):
    class Slow:
        def submit(self, *args, **kwargs):
            return {'id': kwargs['job_id'], 'guard': {'passed': True}, 'meta': {}, 'job': {}}
        def poll(self, handle):
            time.sleep(.5)
            return {'state': 'failed', 'error': 'The warehouse exceeded its time limit; ask a narrower question.'}
        def cancel(self, handle):
            pass
    monkeypatch.setenv('QUESTION_RUN_TIMEOUT', '.3')
    monkeypatch.setenv('STORE_URL', str(tmp_path / 'store'))
    monkeypatch.setenv('QUESTION_STATE_BACKEND', 'store')
    c, _ = run_setup(monkeypatch, Slow())
    if stage == 'assembly':
        def slow(*args, **kwargs):
            time.sleep(.5)
            from kge.adapters.deadline import request_remaining
            request_remaining()
            return {'question': 'Metric', 'answer_text': '15', 'run_trace': {}}
        monkeypatch.setattr(service, 'ask', slow)
    profile = SimpleNamespace(client='sample')
    run = service.start_run(c, profile, Ask(question='Metric'), 'analyst', PRESETS['graph'], wait=1)
    outcomes = [(run['state'], run.get('error'))]
    time.sleep(.4)
    for _ in range(2):
        polled = service.get_run(c, profile, run['id'], 'analyst')
        outcomes.append((polled['state'], polled.get('error')))
        assert 'answer' not in polled or polled['answer'] is None
    assert outcomes[0] == (('expired', questionrun.TIME_LIMIT) if stage == 'assembly' else ('running', None))
    assert outcomes[1:] == [('expired', questionrun.TIME_LIMIT)] * 2
