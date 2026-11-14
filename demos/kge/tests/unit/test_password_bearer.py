"""Password bearer exchange shares sign-in limits and works across instances without sharing local tokens."""
import base64
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from kge.services import http
from kge.services.identity import routes, service


@pytest.fixture
def install(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-key-" * 3)
    monkeypatch.setenv("SIGNIN_PASSWORDS", json.dumps({"reader@example.com": service.hash_password("right"),
                                                      "machine@example.com": service.hash_password("right")}))
    ids = service.Identities(tmp_path / "one", ["reader@example.com", "machine@example.com"], service.SignedSessions(tmp_path / "one", "demo"))
    graph = SimpleNamespace(processes=lambda who: ["querying"] if who == "reader@example.com" else ["tagging"],
                            principals=lambda: [{"id": "reader@example.com"}], principal=lambda who: {"id": who})
    state = SimpleNamespace(PASSWORDS=True, THROTTLE=service.Throttle(), ids=lambda: ids,
                            engine=lambda: SimpleNamespace(g=graph), COOKIE="session", SIGN_IN="sign in")
    monkeypatch.setattr(routes, "served", state)
    monkeypatch.setattr(http, "served", state)
    app = FastAPI()
    app.include_router(routes.router)

    @app.get("/mcp-test")
    def who(request: Request):
        return {"principal": http.mcp_principal(request)}

    with TestClient(app) as client:
        yield client, state, ids


def exchange(client):
    reply = client.post("/session/token", json={"username": "reader@example.com", "password": "right"})
    assert reply.status_code == 200, reply.text
    assert reply.headers["cache-control"] == "no-store" and "set-cookie" not in reply.headers
    assert reply.json()["expires_in"] == 900 and reply.json()["token_type"] == "Bearer"
    return reply.json()["access_token"]


def test_token_cross_instance_scope_expiry_and_tampering(install, tmp_path, monkeypatch):
    client, state, ids = install
    now = [1000]
    monkeypatch.setattr(service.time, "time", lambda: now[0])
    token = exchange(client)
    payload, signature = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    assert claims["p"] == "reader@example.com" and claims["exp"] == 1900
    assert "right" not in json.dumps(claims) and "password" not in claims
    peer = service.Identities(tmp_path / "two", ["reader@example.com"], service.SignedSessions(tmp_path / "two", "demo"))
    assert not set(ids.by_token) & set(peer.by_token)
    state.ids = lambda: peer
    for header in ("authorization", "x-kge-authorization"):
        assert client.get("/mcp-test", headers={header: f"Bearer {token}", "x-serverless-authorization": "Bearer google"}).json() == {"principal": "reader@example.com"}
    for broken in (payload[:-1] + "A." + signature, payload + ".bad", "garbage", ""):
        assert client.get("/mcp-test", headers={"authorization": f"Bearer {broken}"}).status_code == 401
    assert service.SignedBearer(tmp_path / "other", "other").get(token) is None
    monkeypatch.setenv("SESSION_SECRET", "different-session-key-" * 3)
    assert service.SignedBearer(tmp_path / "key", "demo").get(token) is None
    now[0] = 1900
    assert client.get("/mcp-test", headers={"authorization": f"Bearer {token}"}).status_code == 401


def test_cookies_are_not_bearers_and_old_tokens_still_work(install):
    client, state, ids = install
    cookie = client.post("/session", json={"username": "reader@example.com", "password": "right"})
    assert cookie.status_code == 200
    sid = client.cookies.get("session")
    assert client.get("/mcp-test").status_code == 401
    assert client.get("/mcp-test", headers={"authorization": f"Bearer {sid}"}).status_code == 401
    old = next(t for t, p in ids.by_token.items() if p == "reader@example.com")
    assert client.get("/mcp-test", headers={"authorization": f"Bearer {old}"}).status_code == 200
    token = exchange(client)
    assert ids.sessions.get(token) is None
    client.delete("/session")
    assert client.get("/mcp-test", headers={"authorization": f"Bearer {token}"}).status_code == 200
    ids.people = lambda: {"machine@example.com"}
    assert client.get("/mcp-test", headers={"authorization": f"Bearer {token}"}).status_code == 401


@pytest.mark.parametrize("first,second", [("/session", "/session/token"), ("/session/token", "/session")])
def test_both_signins_share_lockout(install, first, second):
    client, state, ids = install
    for _ in range(service.Throttle.FAILURES):
        assert client.post(first, json={"username": "reader@example.com", "password": "wrong"}).status_code == 401
    reply = client.post(second, json={"username": "reader@example.com", "password": "right"})
    assert reply.status_code == 429 and int(reply.headers["retry-after"]) > 0


def test_password_exchange_refuses_machine_unknown_and_disabled_signin(install):
    client, state, ids = install
    for who in ("machine@example.com", "unknown@example.com"):
        assert client.post("/session/token", json={"username": who, "password": "right"}).status_code == 401
    assert client.post("/session/token", json={"username": "reader@example.com", "password": "right", "principal": "admin@example.com"}).status_code == 422
    state.PASSWORDS = False
    assert client.post("/session/token", json={"username": "reader@example.com", "password": "right"}).status_code == 422


def test_bearer_cannot_be_exchanged_for_a_browser_session(install, monkeypatch):
    client, state, ids = install
    now = [1000]
    monkeypatch.setattr(service.time, "time", lambda: now[0])
    token = exchange(client)
    now[0] = 1899
    assert ids.from_token(token) == "reader@example.com"
    assert ids.open_session(token) is None
    reply = client.post("/session", json={"token": token})
    assert reply.status_code == 401 and "set-cookie" not in reply.headers
    assert not client.cookies
    now[0] = 1901
    assert ids.resolve(f"Bearer {token}", None) is None
    local = next(t for t, p in ids.by_token.items() if p == "reader@example.com")
    assert client.post("/session", json={"token": local}).status_code == 200
