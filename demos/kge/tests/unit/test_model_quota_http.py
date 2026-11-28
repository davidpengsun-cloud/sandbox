import json
import threading
from types import SimpleNamespace

import anyio
import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from mcp.server.fastmcp.exceptions import ToolError

from kge.adapters.model.quota import admit
from kge.services import http
from kge.services.ask import service


@pytest.fixture
def authenticated(monkeypatch, tmp_path):
    monkeypatch.setenv("MODEL_RATE_LIMITS", '{"default":1,"roles":{"analyst":1}}')
    monkeypatch.setenv("MODEL_QUOTA_STORE_URL", str(tmp_path))
    roles = {"alice": {"analyst"}, "bob": {"analyst"}, "worker": {"machine"}}
    graph = SimpleNamespace(acting_roles=lambda who: roles[who],
                            rights=lambda: SimpleNamespace(processes={"analyst": {"querying"}, "machine": {"model"}}))
    ids = SimpleNamespace(resolve=lambda auth, cookie: auth.removeprefix("Bearer ") if auth in
                          ("Bearer alice", "Bearer bob", "Bearer worker") else None,
                          allows=lambda who: who in roles)
    served = SimpleNamespace(engine=lambda: SimpleNamespace(g=graph), PROFILE=SimpleNamespace(client="meridian"),
                             ids=lambda: ids, COOKIE="session", SIGN_IN="Please sign in")
    monkeypatch.setattr(http, "served", served)
    return graph


def test_real_http_middleware_binds_caller_and_returns_429_without_leaking_identity(authenticated, monkeypatch):
    monkeypatch.setenv("KGE_QUOTA_JOB_CAP", "not-a-valid-job-capability")
    app = FastAPI()
    app.add_middleware(http.RequestLimitsMiddleware)
    sent = []

    @app.post("/ask")
    def ask():
        admit("test:model", "plan")
        sent.append(True)
        return {"accepted": True}

    with TestClient(app) as client:
        assert client.post("/ask", headers={"Authorization": "Bearer alice"}, json={}).status_code == 200
        denied = client.post("/ask", headers={"Authorization": "Bearer alice"}, json={})
        assert denied.status_code == 429
        assert 1 <= int(denied.headers["Retry-After"]) <= 60
        assert "request limit" in denied.json()["detail"]
        assert "alice" not in denied.text
        assert client.post("/ask", headers={"Authorization": "Bearer bob"}, json={}).status_code == 200
    assert len(sent) == 2


def test_role_read_failure_refuses_before_model_work(authenticated, monkeypatch):
    app = FastAPI()
    app.add_middleware(http.RequestLimitsMiddleware)
    sent = []
    monkeypatch.setattr(authenticated, "acting_roles", lambda who: (_ for _ in ()).throw(RuntimeError("secret")))

    @app.post("/ask")
    def ask():
        sent.append(True)
        return {"accepted": True}

    with TestClient(app) as client:
        response = client.post("/ask", headers={"Authorization": "Bearer alice"}, json={})
    assert response.status_code == 503
    assert "secret" not in response.text
    assert not sent


def test_revoked_person_cannot_be_reclassified_as_a_new_service(authenticated, monkeypatch):
    app = FastAPI()
    app.add_middleware(http.RequestLimitsMiddleware)
    sent = []

    @app.post("/ask")
    def ask():
        admit("test:model", "plan")
        sent.append(True)
        monkeypatch.setattr(authenticated, "acting_roles", lambda who: set())
        admit("test:model", "wording")
        sent.append(True)
        return {"accepted": True}

    with TestClient(app) as client:
        response = client.post("/ask", headers={"Authorization": "Bearer alice"}, json={})
    assert response.status_code == 503
    assert len(sent) == 1


def test_mcp_tool_uses_authenticated_identity_and_surfaces_quota(authenticated, monkeypatch):
    monkeypatch.setattr(http, "mcp_principal", lambda request: "alice")
    server = http.mcp_server("quota-test")

    @server.tool(meta={"route": "POST /ask"})
    async def probe() -> dict:
        admit("test:model", "plan")
        return {"accepted": True}

    anyio.run(server._tool_manager.call_tool, "probe", {})
    with pytest.raises(ToolError, match="429: .*request limit"):
        anyio.run(server._tool_manager.call_tool, "probe", {})


def test_ask_stream_thread_inherits_identity_and_sends_plain_refusal(authenticated):
    app = FastAPI()
    app.add_middleware(http.RequestLimitsMiddleware)

    @app.post("/ask")
    def ask():
        admit("test:model", "plan")
        async def connected():
            return False
        return StreamingResponse(service.stream("test", lambda on_stage: admit("test:model", "plan"), connected),
                                 media_type="application/x-ndjson")

    with TestClient(app) as client:
        response = client.post("/ask", headers={"Authorization": "Bearer alice"}, json={})
    events = [json.loads(line) for line in response.text.splitlines()]
    error = next(event for event in events if event.get("event") == "error")
    assert error["status"] == 429
    assert "request limit" in error["error"]
    assert "alice" not in response.text


def test_evaluation_worker_rechecks_revoked_roles_before_model_call(authenticated, monkeypatch):
    from kge.pipeline.trace import carried
    from kge.services.onboard import jobs
    saved = []
    monkeypatch.setattr(jobs, "save", lambda app, job: saved.append(dict(job)))
    job = {"id": "evaluation", "by": "alice"}
    app = SimpleNamespace(PROFILE=SimpleNamespace(client="meridian"))
    calls = []

    def runner():
        admit("test:model", "plan")
        calls.append(True)

    with http.quota_context("alice"):
        worker = carried(lambda: jobs._run_eval(app, job, runner))
    monkeypatch.setattr(authenticated, "acting_roles", lambda who: set())
    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert not calls
    assert saved[-1]["state"] == "error"
    assert "cannot verify the caller" in saved[-1]["message"]
