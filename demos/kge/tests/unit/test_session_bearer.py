"""POST /session/bearer: the trusted portal exchanges a person's session cookie for a short bearer tied to that session."""
import base64
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from kge.services import http
from kge.services.identity import routes, service

PERSON = "reader@example.com"
CALLER = {"x-caller-token": "portal-id-token"}


def claims(token):
    body = token.split(".")[0]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def app_for(state):
    app = FastAPI()
    app.include_router(routes.router)

    @app.get("/mcp-test")
    def who(request: Request):
        return {"principal": http.mcp_principal(request)}

    return app


@pytest.fixture(params=["signed", "demo"])
def install(request, tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-key-" * 3)
    monkeypatch.setenv("TRUSTED_CALLER_AUDIENCE", "https://kge.example")
    monkeypatch.setenv("SIGNIN_PASSWORDS", json.dumps({PERSON: service.hash_password("right")}))
    signed = request.param == "signed"
    ids = service.Identities(tmp_path, [PERSON], service.SignedSessions(tmp_path, "demo") if signed else None)
    graph = SimpleNamespace(processes=lambda who: ["querying"], principals=lambda: [{"id": PERSON}], principal=lambda who: {"id": who})
    state = SimpleNamespace(PASSWORDS=True, THROTTLE=service.Throttle(), ids=lambda: ids, engine=lambda: SimpleNamespace(g=graph),
                            COOKIE="session", SIGN_IN="sign in",
                            TRUSTED_CALLER=SimpleNamespace(trusts=lambda token: token == CALLER["x-caller-token"]))
    monkeypatch.setattr(routes, "served", state)
    monkeypatch.setattr(http, "served", state)
    with TestClient(app_for(state)) as client:
        sid, _ = ids.demo_session(PERSON)
        client.cookies.set("session", sid)
        yield client, ids, signed


def exchange(client, headers=CALLER):
    return client.post("/session/bearer", headers=headers)


def test_an_untrusted_caller_is_refused(install):
    client, _, _ = install
    for headers in ({}, {"x-caller-token": "someone-else"}, {"x-portal-sync-token": "guess"}):
        reply = exchange(client, headers)
        assert reply.status_code == 403 and "access_token" not in reply.text


def test_no_cookie_or_a_bearer_alone_is_refused(install):
    client, _, _ = install
    bearer = exchange(client).json()["access_token"]
    client.cookies.clear()
    assert exchange(client).status_code == 401
    for header in ("authorization", "x-kge-authorization"):
        assert exchange(client, {**CALLER, header: f"Bearer {bearer}"}).status_code == 401
    client.cookies.set("session", "not-a-session")
    assert exchange(client).status_code == 401


def test_mcp_accepts_the_bearer_as_the_person(install):
    client, ids, _ = install
    reply = exchange(client)
    assert reply.status_code == 200, reply.text
    assert reply.headers["cache-control"] == "no-store" and "set-cookie" not in reply.headers
    token = reply.json()
    assert token["token_type"] == "Bearer" and 0 < token["expires_in"] <= 900
    cookie = client.cookies.get("session")
    client.cookies.clear()
    for header in ("authorization", "x-kge-authorization"):
        assert client.get("/mcp-test", headers={header: f"Bearer {token['access_token']}"}).json() == {"principal": PERSON}
    assert ids.sessions.get(token["access_token"]) is None
    assert cookie not in token["access_token"]


def test_a_signed_out_sessions_bearer_is_refused_on_mcp(install):
    client, _, _ = install
    bearer = exchange(client).json()["access_token"]
    other = TestClient(client.app)
    assert other.get("/mcp-test", headers={"x-kge-authorization": f"Bearer {bearer}"}).status_code == 200
    assert client.delete("/session").status_code == 200
    assert other.get("/mcp-test", headers={"x-kge-authorization": f"Bearer {bearer}"}).status_code == 401


def test_expiry_never_exceeds_the_sessions(install, monkeypatch):
    client, ids, signed = install
    now = [1_000_000]
    monkeypatch.setattr(service.time, "time", lambda: now[0])
    if signed:
        session_exp = now[0] + 300
        client.cookies.set("session", ids.sessions.open(PERSON, exp=session_exp))
        token = exchange(client).json()
        assert token["expires_in"] == 300 and claims(token["access_token"])["exp"] == session_exp
        now[0] = session_exp - 900 + 1000
        client.cookies.set("session", ids.sessions.open(PERSON))
        token = exchange(client).json()
        assert token["expires_in"] == 900 and claims(token["access_token"])["exp"] == now[0] + 900
    else:
        token = exchange(client).json()
        assert token["expires_in"] == 900
    bearer = token["access_token"]
    other = TestClient(client.app)
    now[0] += token["expires_in"] - 1
    assert other.get("/mcp-test", headers={"authorization": f"Bearer {bearer}"}).status_code == 200
    now[0] += 1
    assert other.get("/mcp-test", headers={"authorization": f"Bearer {bearer}"}).status_code == 401


def test_a_session_bearer_opens_no_session(install):
    client, ids, _ = install
    bearer = exchange(client).json()["access_token"]
    assert ids.sessions.get(bearer) is None
    client.cookies.set("session", bearer)
    assert exchange(client).status_code == 401
