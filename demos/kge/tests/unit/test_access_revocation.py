"""Every credential checks live grants at the next request after the cache expires."""
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from kge.adapters.appstate.files import FilesState
from kge.adapters.objstore import open_store
from kge.adapters.deadline import ExternalCallError
from kge.pipeline.access_table import Access
from kge.services import http
from kge.services.identity import routes, service


@pytest.fixture
def install(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-key-" * 3)
    monkeypatch.delenv("ACCESS_ADMINS", raising=False)
    monkeypatch.setenv("SIGNIN_PASSWORDS", json.dumps({"ann@example.com": service.hash_password("right")}))
    state = FilesState(None, open_store(str(tmp_path / "access")))
    state.access_put({"username": "ann@example.com", "name": "Ann", "roles": ["reader"]}, by="operator@example.com", via="job")
    now = [0.0]
    access = Access(state, clock=lambda: now[0])
    ids = service.Identities(tmp_path / "run", access.people, service.SignedSessions(tmp_path / "run", "acme"), access=access)
    graph = SimpleNamespace(principal=lambda who: {"id": who, "role": "Reader"},
                            processes=lambda who: {"querying"} if "reader" in access.roles_of(who) else set())
    served = SimpleNamespace(PASSWORDS=True, THROTTLE=service.Throttle(), ids=lambda: ids, engine=lambda: SimpleNamespace(g=graph),
                             COOKIE="session", SIGN_IN="sign in", TRUSTED_CALLER=None)
    monkeypatch.setattr(routes, "served", served)
    monkeypatch.setattr(http, "served", served)
    app = FastAPI()
    app.include_router(routes.router)

    @app.get("/mcp-subject")
    def mcp_subject(request: Request):
        return {"id": http.mcp_principal(request)}

    @app.exception_handler(ExternalCallError)
    def unavailable(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=503)

    with TestClient(app) as client:
        yield client, state, now, ids


@pytest.mark.parametrize("change", ["delete", "empty"])
def test_session_and_both_bearers_stop_at_sixty_seconds(install, change):
    client, state, now, ids = install
    assert client.post("/session", json={"username": "ann@example.com", "password": "right"}).status_code == 200
    signed = client.post("/session/token", json={"username": "ann@example.com", "password": "right"}).json()["access_token"]
    local = ids.token_for("ann@example.com")
    if change == "delete":
        state.access_delete("ann@example.com", by="operator@example.com", via="job")
    else:
        state.access_put({"username": "ann@example.com", "name": "Ann", "roles": []}, by="operator@example.com", via="job")
    now[0] = 59.999
    assert client.get("/whoami").status_code == 200
    now[0] = 60
    assert client.get("/whoami").status_code == 401
    for token in (local, signed):
        assert client.get("/whoami", headers={"Authorization": f"Bearer {token}"}).status_code == 401
        assert ids.from_token(token) is None
        assert client.get("/mcp-subject", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert client.post("/session", json={"username": "ann@example.com", "password": "right"}).status_code == 401
    assert ids.demo_session("ann@example.com") is None


def test_new_email_lazy_token_display_and_admin_union(install, monkeypatch):
    client, state, now, ids = install
    assert client.post("/session", json={"username": "ann@example.com", "password": "right"}).json()["name"] == "Ann"
    state.access_put({"username": "new@example.com", "name": "New Name", "roles": ["access_admin"]}, by="operator@example.com", via="job")
    monkeypatch.setenv("SIGNIN_PASSWORDS", json.dumps({"new@example.com": service.hash_password("new")}))
    now[0] = 60
    result = client.post("/session", json={"username": " NEW@EXAMPLE.COM ", "password": "new"})
    assert result.status_code == 200 and result.json()["roles"] == ["access_admin"]
    assert result.json()["name"] == "New Name" and result.json()["admin"] is True
    token = ids.token_for("new@example.com")
    assert token and service.token_for(ids.folder, "new@example.com") == token
    state.access_delete("new@example.com", by="operator@example.com", via="job")
    now[0] = 120
    assert client.get("/whoami").status_code == 401


def test_password_source_failure_returns_plain_503_and_retries(install):
    client, state, now, ids = install
    calls = []

    def failed():
        calls.append(1)
        raise ExternalCallError("Sign-in passwords unavailable: Secret Manager HTTP 403")

    ids.passwords = SimpleNamespace(hashes=failed)
    for route in ("/session", "/session/token"):
        result = client.post(route, json={"username": "ann@example.com", "password": "right"})
        assert result.status_code == 503
        assert result.json() == {"detail": "Sign-in passwords unavailable: Secret Manager HTTP 403"}
    assert len(calls) == 2


def test_role_scoped_agent_credentials_do_not_read_people(tmp_path):
    def unavailable_people():
        pytest.fail("the gate must not read the access table")

    ids = service.Identities(tmp_path, unavailable_people, role_ids=["reader"])
    token = ids.token_for("reader")
    assert ids.from_token(token) == "reader"
    sid, who = ids.demo_session("reader")
    assert who == "reader" and ids.resolve(None, sid) == "reader"


def test_bootstrap_admin_without_a_row_can_sign_in(install, monkeypatch):
    client, state, now, ids = install
    monkeypatch.setenv("ACCESS_ADMINS", "bootstrap@example.com")
    monkeypatch.setenv("SIGNIN_PASSWORDS", json.dumps({"bootstrap@example.com": service.hash_password("bootstrap")}))
    result = client.post("/session", json={"username": "bootstrap@example.com", "password": "bootstrap"})
    assert result.status_code == 200
    assert result.json()["admin"] is True and result.json()["roles"] == ["access_admin"]
    assert "bootstrap@example.com" not in {row["username"] for row in state.access_grants()}
    monkeypatch.setenv("ACCESS_ADMINS", "")
    assert client.get("/whoami").status_code == 401


def test_agent_role_tokens_never_consult_people(tmp_path):
    from kge.services.ask.engine.agent import Agent

    def unavailable_people():
        pytest.fail("a gate agent must not read the access table")

    from kge.services.ask.engine.graphview import Graph

    graph = Graph.__new__(Graph)
    graph._roles_only = True
    graph.role_ids = lambda: {"reader"}
    graph.rights = lambda: SimpleNamespace(roles={"reader": "Reader"})
    graph.people = SimpleNamespace(people=unavailable_people)
    engine = SimpleNamespace(g=graph, profile=SimpleNamespace(run_dir=lambda: tmp_path), UNIT_ORDER=[])
    agent = Agent(engine, llm=None)
    token = agent.token("reader")
    assert agent.identities.from_token(token) == "reader"


@pytest.mark.parametrize("supplied,canonical", [
    ("  ＡＮＮ@EXAMPLE.COM  ", "ann@example.com"),
    ("ＦＲＥＥ／ＵＳＥＲ", "free/user"),
    ("Straße", "strasse"),
    ("u" * 254, "u" * 254),
])
@pytest.mark.parametrize("route", ["/session", "/session/token"])
def test_password_signin_normalizes_and_accepts_freeform_table_usernames(install, monkeypatch, supplied, canonical, route):
    client, state, now, ids = install
    state.access_put({"username": canonical, "name": "Test person", "roles": ["reader"]},
                     by="operator@example.com", via="job")
    monkeypatch.setenv("SIGNIN_PASSWORDS", json.dumps({canonical: service.hash_password("right")}))
    now[0] = 60
    result = client.post(route, json={"username": supplied, "password": "right"})
    assert result.status_code == 200, result.text
    if route == "/session":
        assert result.json()["id"] == canonical
        assert client.get("/whoami").json()["id"] == canonical
    else:
        assert ids.from_token(result.json()["access_token"]) == canonical


@pytest.mark.parametrize("route", ["/session", "/session/token"])
def test_unknown_username_and_wrong_password_have_the_same_reply(install, route):
    client, _, _, _ = install
    wrong = client.post(route, json={"username": "ann@example.com", "password": "wrong"})
    unknown = client.post(route, json={"username": "unknown@example.com", "password": "wrong"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "wrong username or password"}
    assert wrong.headers.get("retry-after") == unknown.headers.get("retry-after")
    assert "set-cookie" not in wrong.headers and "set-cookie" not in unknown.headers


def test_username_aliases_share_the_existing_signin_throttle(install):
    client, _, _, _ = install
    for supplied in ["ann@example.com", "ANN@EXAMPLE.COM", " ＡＮＮ@example.com ", "Ann@Example.com", "ann@example.com"]:
        assert client.post("/session", json={"username": supplied, "password": "wrong"}).status_code == 401
    locked = client.post("/session/token", json={"username": "ANN@EXAMPLE.COM", "password": "right"})
    assert locked.status_code == 429 and locked.headers["retry-after"] == "300"


def test_username_equal_to_role_is_still_resolved_only_through_table(install, monkeypatch):
    client, state, now, ids = install
    state.access_put({"username": "reader", "name": "Role collision", "roles": ["reader"]},
                     by="operator@example.com", via="job")
    monkeypatch.setenv("SIGNIN_PASSWORDS", json.dumps({"reader": service.hash_password("right")}))
    now[0] = 60
    assert client.post("/session", json={"username": "reader", "password": "right"}).status_code == 200
    token = ids.token_for("reader")
    state.access_delete("reader", by="operator@example.com", via="job")
    now[0] = 120
    assert client.get("/whoami").status_code == 401
    assert ids.from_token(token) is None and ids.demo_session("reader") is None
    assert client.post("/session/token", json={"username": "reader", "password": "right"}).status_code == 401
