"""Three independent API instances share only their store, never their run folders."""
from contextlib import ExitStack
from contextvars import ContextVar
from concurrent.futures import ThreadPoolExecutor
import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from kge.adapters.sessionstate import for_profile as identity_state
from kge.adapters.appstate import bigquery as adapter
from kge.adapters.deadline import ExternalCallError
from kge.ports import StoreError
from kge.services import http
from kge.services.identity import routes, service
from tests import bigquery_fake


@pytest.mark.parametrize("backend", ["files", "bigquery"])
def test_three_api_instances_share_cookies_sign_out_and_personal_tokens(tmp_path, monkeypatch, backend):
    monkeypatch.setenv("APP_STATE_BACKEND", backend)
    monkeypatch.delenv("SESSION_STATE_BACKEND", raising=False)
    monkeypatch.setenv("STORE_URL", str(tmp_path / "shared"))
    monkeypatch.setenv("BIGQUERY_PROJECT", bigquery_fake.PROJECT)
    monkeypatch.setenv("BIGQUERY_DATASET_PREFIX", "kge")
    monkeypatch.setenv("SESSION_SECRET", "three-instance-key-" * 3)
    monkeypatch.setattr(adapter, "_IDENTITY_ENSURED", set())
    fake, jobs = bigquery_fake.FakeBigQuery(), []

    def post(self, body):
        jobs.append(body["query"])
        return fake.state_post(body)

    monkeypatch.setattr(adapter.BigQueryState, "_post", post)
    current = ContextVar("identity_instance")

    class CurrentInstance:
        def __getattr__(self, name):
            return getattr(current.get(), name)

    for module in (http, routes):
        monkeypatch.setattr(module, "served", CurrentInstance())
    now, people = [100.0], {"reader@example.com"}
    clients, identities = [], []
    with ExitStack() as stack:
        for number in range(3):
            run = tmp_path / str(number) / "run"
            prof = SimpleNamespace(client="sample", run_dir=lambda: run)
            state = identity_state(prof)
            state.identity_clock = lambda: now[0]
            state.refresh_identity()
            ids = service.Identities(run, lambda: people, service.SignedSessions(run, "sample", state=state), state=state)
            identities.append(ids)
            graph = SimpleNamespace(principal=lambda who: {"id": who, "reads": ["sample"]},
                                    readable_units=lambda who: ["sample"])
            served = SimpleNamespace(ids=lambda ids=ids: ids, COOKIE="session", SIGN_IN="sign in",
                                     engine=lambda graph=graph: SimpleNamespace(g=graph))
            app = FastAPI()
            app.include_router(routes.router)

            @app.middleware("http")
            async def instance(request, call_next, served=served):
                token = current.set(served)
                try:
                    return await call_next(request)
                finally:
                    current.reset(token)

            @app.exception_handler(ExternalCallError)
            async def unavailable(request, exc):
                return JSONResponse({"detail": str(exc)}, status_code=503)

            clients.append(stack.enter_context(TestClient(app)))

        assert len({id(ids.state) for ids in identities}) == 3
        personal = identities[0].token_for("reader@example.com")
        now[0] += 3
        for ids in identities:
            ids.state.refresh_identity()
        for client in clients:
            assert client.post("/session", json={"token": personal}).status_code == 200
        cookies = [client.cookies.get("session") for client in clients]
        for client in clients:
            client.cookies.clear()
        for cookie in cookies:
            for client in clients:
                assert client.get("/whoami", headers={"Cookie": f"session={cookie}"}).json()["id"] == "reader@example.com"

        for _ in range(6):
            now[0] += 4
            before = len(jobs)
            for client in clients:
                for _ in range(20):
                    assert client.get("/whoami", headers={"Authorization": f"Bearer {personal}"}).status_code == 200
                    assert client.get("/readable-units", headers={"Cookie": f"session={cookies[0]}"}).json() == {"units": ["sample"]}
            assert len(jobs) == before, "authentication adds zero BigQuery jobs across refresh intervals"
            for ids in identities:
                ids.state.refresh_identity()
            assert len(jobs) - before == 0

        for index, cookie in enumerate(cookies):
            assert clients[index].delete("/session", headers={"Cookie": f"session={cookie}"}).status_code == 200
            assert clients[index].get("/whoami", headers={"Cookie": f"session={cookie}"}).status_code == 401
        now[0] += 3
        for ids in identities:
            ids.state.refresh_identity()
        for client in clients:
            for cookie in cookies:
                assert client.get("/whoami", headers={"Cookie": f"session={cookie}"}).status_code == 401
            assert client.get("/whoami", headers={"Authorization": f"Bearer {personal}"}).status_code == 200

        fresh_cookie = identities[0].sessions.open("reader@example.com")
        signed_bearer = identities[0].bearer_token("reader@example.com")["access_token"]
        now[0] += 30
        before = len(jobs)

        def failed_refresh(*args):
            raise StoreError("private backend diagnostic")

        with monkeypatch.context() as failed:
            for ids in identities:
                failed.setattr(ids.state.store, "get_version", failed_refresh)
            for client in clients:
                for headers in ({"Cookie": f"session={fresh_cookie}"}, {"Authorization": f"Bearer {personal}"},
                                {"Authorization": f"Bearer {signed_bearer}"}):
                    refused = client.get("/whoami", headers=headers)
                    assert refused.status_code == 503
                    assert refused.json() == {"detail": "Sign-in state unavailable: snapshot is missing or at least 30 seconds old; retry shortly"}
        assert len(jobs) == before
        for ids in identities:
            ids.state.refresh_identity()
        assert len({ids.sessions.fingerprint for ids in identities}) == 1
        people.clear()
        for client in clients:
            assert client.get("/whoami", headers={"Authorization": f"Bearer {personal}"}).status_code == 401


@pytest.fixture(params=['files', 'bigquery'])
def idle_api(request, tmp_path, monkeypatch):
    monkeypatch.setenv('APP_STATE_BACKEND', request.param)
    monkeypatch.delenv('SESSION_STATE_BACKEND', raising=False)
    monkeypatch.setenv('STORE_URL', str(tmp_path / 'shared'))
    monkeypatch.setenv('BIGQUERY_PROJECT', bigquery_fake.PROJECT)
    monkeypatch.setenv('SESSION_SECRET', 'idle-instance-key-' * 3)
    monkeypatch.setattr(adapter, '_IDENTITY_ENSURED', set())
    fake, jobs, now = bigquery_fake.FakeBigQuery(), [], [100.0]

    def post(self, body):
        jobs.append(body['query'])
        return fake.state_post(body)

    monkeypatch.setattr(adapter.BigQueryState, '_post', post)
    run = tmp_path / 'run'
    state = identity_state(SimpleNamespace(client='sample', run_dir=lambda: run))
    state.identity_clock = lambda: now[0]
    state.refresh_identity()
    ids = service.Identities(run, lambda: {'reader@example.com'}, service.SignedSessions(run, 'sample', state=state), state=state)
    personal = ids.token_for('reader@example.com')
    cookie = ids.sessions.open('reader@example.com')
    bearer = ids.bearer_token('reader@example.com')['access_token']
    graph = SimpleNamespace(principal=lambda who: {'id': who, 'reads': ['sample']})
    served = SimpleNamespace(ids=lambda: ids, COOKIE='session', SIGN_IN='sign in', engine=lambda: SimpleNamespace(g=graph))
    for module in (http, routes):
        monkeypatch.setattr(module, 'served', served)
    app = FastAPI()
    app.include_router(routes.router)

    @app.exception_handler(ExternalCallError)
    async def unavailable(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=503)

    headers = [{'Cookie': f'session={cookie}'}, {'Authorization': f'Bearer {personal}'}, {'Authorization': f'Bearer {bearer}'}]
    with TestClient(app) as client:
        jobs.clear()
        yield client, state, now, jobs, headers, 0


def test_idle_sixty_seconds_refreshes_once_then_answers_every_credential(idle_api):
    client, _, now, jobs, headers, budget = idle_api
    for _ in range(2):
        now[0] += 60
        before = len(jobs)
        for credential in headers:
            result = client.get('/whoami', headers=credential)
            assert result.status_code == 200
            assert result.json()['id'] == 'reader@example.com'
        assert len(jobs) - before == budget


def test_ten_concurrent_requests_after_idle_share_one_refresh_job(idle_api):
    client, _, now, jobs, headers, budget = idle_api
    now[0] += 60
    start = threading.Barrier(10)

    def whoami(index):
        start.wait(timeout=5)
        return client.get('/whoami', headers=headers[index % len(headers)])

    with ThreadPoolExecutor(max_workers=10) as workers:
        responses = list(workers.map(whoami, range(10)))
    assert [response.status_code for response in responses] == [200] * 10
    assert all(response.json()['id'] == 'reader@example.com' for response in responses)
    assert len(jobs) == budget


def test_idle_refresh_observes_another_instances_sign_out(idle_api, tmp_path):
    client, _, now, jobs, headers, budget = idle_api
    peer = identity_state(SimpleNamespace(client='sample', run_dir=lambda: tmp_path / 'peer'))
    service.SignedSessions(tmp_path / 'peer', 'sample', state=peer).close(headers[0]['Cookie'].split('=', 1)[1])
    jobs.clear()
    now[0] += 60
    assert client.get('/whoami', headers=headers[0]).status_code == 401
    for credential in headers[1:]:
        assert client.get('/whoami', headers=credential).status_code == 200
    assert len(jobs) == budget


def test_steady_requests_add_zero_identity_jobs(idle_api):
    client, state, now, jobs, headers, budget = idle_api
    for _ in range(12):
        now[0] += 3
        before = len(jobs)
        for _ in range(20):
            for credential in headers:
                assert client.get('/whoami', headers=credential).status_code == 200
        assert len(jobs) == before
        state.refresh_identity()
        assert len(jobs) - before == budget


def test_failed_idle_refresh_returns_safe_503_instead_of_old_credentials(idle_api, monkeypatch):
    client, state, now, jobs, headers, budget = idle_api
    attempts = []

    def unavailable(*args):
        attempts.append(True)
        raise StoreError('private backend diagnostic')

    monkeypatch.setattr(state.store, 'get_version', unavailable)
    now[0] += 60
    for credential in headers:
        before = len(attempts)
        result = client.get('/whoami', headers=credential)
        assert result.status_code == 503
        assert result.json() == {'detail': 'Sign-in state unavailable: snapshot is missing or at least 30 seconds old; retry shortly'}
        assert len(attempts) - before == 1
    assert len(jobs) == budget * len(headers)
