"""Interactive unit writes cannot race a job's staged capture and restore."""
from types import SimpleNamespace
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kge.adapters.objstore import open_store
from kge import packstore
from kge.adapters import appstate
from kge.estate.onboard import review, stage
from kge.pipeline import access_table
from kge.pipeline import lease
from kge.services.onboard import jobs, mcp, routes
from kge.services import http
from kge.services.ask.mcp import MCPEndpoint, tool
from kge.services.identity import Identities, token_for
from tests.unit.test_onboard_api import client
from tests.unit import test_release_audit

review_prof = test_release_audit.prof


WRITES = [
    ("source", "create", "knowledge_engineer.meridian@example.com",
     {"unit": "leasing", "label": "Leasing", "ddl": "CREATE TABLE t (id INTEGER)"}),
    ("rows", "rows", "knowledge_engineer.meridian@example.com", {"unit": "leasing", "rows": "invented"}),
    ("draft", "draft", "knowledge_engineer.meridian@example.com", {"unit": "leasing"}),
    ("accept", "accept", "business_team.meridian@example.com", {"unit": "leasing"}),
    ("certify", "certify", "steward_glossary.meridian@example.com", {"unit": "leasing"}),
]


@pytest.fixture
def transports(monkeypatch, tmp_path):
    client(monkeypatch, tmp_path)
    fake = routes._app()
    aliases = {"knowledge_engineer.meridian@example.com": "engineer@example.com",
               "business_team.meridian@example.com": "business@example.com",
               "steward_glossary.meridian@example.com": "steward@example.com"}
    permissions, require_process = routes.core.permissions, routes.core.require_process
    monkeypatch.setattr(routes.core, "permissions", lambda prof, who, *a, **k: permissions(prof, aliases[who], *a, **k))
    monkeypatch.setattr(routes.core, "require_process", lambda prof, who, *a, **k: require_process(prof, aliases[who], *a, **k))
    ids = Identities(tmp_path / "tokens", {write[2] for write in WRITES})
    fake.ids = lambda: ids
    fake.COOKIE, fake.SIGN_IN = "session", "sign in"
    fake.principal = http.principal
    monkeypatch.setattr(http, "served", fake)
    server = mcp.mcp_server(tool)

    @asynccontextmanager
    async def lifespan(app):
        async with server.session_manager.run():
            yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(routes.router)
    app.add_route("/mcp/onboard", MCPEndpoint(server), methods=["GET", "POST", "DELETE"])
    with TestClient(app) as c:
        yield c, lambda who: {"Authorization": "Bearer " + token_for(ids.folder, who)}


@pytest.mark.parametrize("transport,path,operation,who,body", [
    (transport, *write) for transport in ("http", "mcp") for write in WRITES
    if transport == "http" or write[0] not in {"accept", "certify"}
])
def test_unit_write_refused_during_lease_and_succeeds_after_release(
        monkeypatch, tmp_path, transports, path, operation, who, body, transport):
    c, headers = transports
    monkeypatch.setenv("STORE_URL", (tmp_path / "objects").as_uri())
    store = open_store((tmp_path / "objects").as_uri())
    writes = []
    monkeypatch.setattr(routes.core, operation, lambda *a, **k: writes.append(operation) or {"unit": "leasing"})
    held = lease.take(store, "meridian", "onboard")
    detail = "a job is running for meridian: try again when it finishes"

    def call():
        response = c.post("/mcp/onboard", headers={"Accept": "application/json, text/event-stream", **headers(who)},
                          json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                "params": {"name": f"onboard_{path}", "arguments": body}})
        assert response.status_code == 200
        return response.json()["result"]

    if transport == "http":
        response = c.post(f"/onboard/{path}", json=body, headers=headers(who))
        assert response.status_code == 409
        assert response.json()["detail"] == detail
    else:
        result = call()
        assert result["isError"]
        assert f"409: {detail}" in result["content"][0]["text"]
    assert writes == []
    lease.release(store, held)
    if transport == "http":
        assert c.post(f"/onboard/{path}", json=body, headers=headers(who)).status_code == 200
    else:
        assert not call().get("isError")
    assert writes == [operation]


def test_local_running_job_refuses_writes(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path)
    monkeypatch.delenv("STORE_URL", raising=False)
    monkeypatch.setattr(jobs, "active", {"running"})
    jobs.save(routes._app(), {"id": "running", "state": "running", "started_at": jobs.now()})
    monkeypatch.setattr(routes.core, "draft", lambda *a, **k: {"items": []})
    result = c.post("/onboard/draft", json={"unit": "leasing"})
    assert result.status_code == 409
    assert result.json()["detail"] == "a job is running for meridian: try again when it finishes"


def test_write_holds_lease_until_it_finishes_and_releases_on_error(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path)
    monkeypatch.setenv("STORE_URL", (tmp_path / "objects").as_uri())
    store = open_store((tmp_path / "objects").as_uri())

    def draft(*args, **kwargs):
        with pytest.raises(lease.Refused):
            lease.take(store, "meridian", "onboard")
        raise routes.core.OnboardError(422, "draft refused")

    monkeypatch.setattr(routes.core, "draft", draft)
    result = c.post("/onboard/draft", json={"unit": "leasing"})
    assert result.status_code == 422
    assert lease.holder(store, "meridian") is None


def test_a_saved_edit_stands_when_its_lease_cannot_be_released(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path)
    monkeypatch.setenv("STORE_URL", (tmp_path / "objects").as_uri())
    monkeypatch.setattr(routes.core, "draft", lambda *a, **k: {"items": []})

    def release(*args, **kwargs):
        raise jobs.StoreError("the store is away")

    monkeypatch.setattr(jobs.lease, "release", release)
    assert c.post("/onboard/draft", json={"unit": "leasing"}).status_code == 200


def test_failed_job_restore_keeps_prior_http_review_and_audit(review_prof, monkeypatch):
    prof = review_prof
    fake = SimpleNamespace(PROFILE=prof, engine=lambda: SimpleNamespace(
        g=SimpleNamespace(people=access_table.Access.for_profile(prof))))
    monkeypatch.setattr(routes, "_app", lambda: fake)
    app = FastAPI()
    app.include_router(routes.router)
    actor = test_release_audit.ACCEPTER
    app.dependency_overrides[routes.signed_in] = lambda: actor
    c = TestClient(app)
    assert c.post("/onboard/accept", json={"unit": "leasing"}).status_code == 200
    store = packstore.for_profile(prof)
    state = appstate.for_profile(prof, store)
    before = {key: store.read(key) for key in store.list("units/")}
    audit = state.history("leasing")
    assert any(entry["by"] == actor and entry["step"] == "accept" for entry in audit)
    monkeypatch.setattr(jobs, "active", {"running"})
    jobs.save(fake, {"id": "running", "state": "running", "started_at": jobs.now()})
    captured = stage.capture(prof, ["leasing"])
    actor = test_release_audit.CERTIFIER
    assert c.post("/onboard/certify", json={"unit": "leasing"}).status_code == 409
    review.certify(prof, "leasing", as_=actor)
    assert state.history("leasing") != audit
    store.write("units/leasing/failed-build", b"discard")
    stage.restore(prof, captured)
    assert {key: store.read(key) for key in store.list("units/")} == before
    assert state.history("leasing") == audit
