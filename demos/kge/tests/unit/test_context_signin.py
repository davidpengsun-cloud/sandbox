"""Context's forwarded sign-ins retain KGE's address-and-username limits on both routes."""
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kge.services import http
from kge.services.identity import routes, service


@pytest.fixture
def context_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-key-" * 3)
    monkeypatch.setenv("SIGNIN_PASSWORDS", json.dumps({"reader@example.com": service.hash_password("right"),
                                                       "other@example.com": service.hash_password("right")}))
    people = {"reader@example.com", "other@example.com"}
    ids = service.Identities(tmp_path, people, service.SignedSessions(tmp_path, "context"))
    graph = SimpleNamespace(processes=lambda who: {"querying"}, principal=lambda who: {"id": who})
    now = [1000]
    state = SimpleNamespace(PASSWORDS=True, THROTTLE=service.Throttle(clock=lambda: now[0]), ids=lambda: ids,
                            engine=lambda: SimpleNamespace(g=graph), COOKIE="session", SIGN_IN="sign in",
                            TRUSTED_CALLER=SimpleNamespace(trusts=lambda token: token == "portal-service"))
    monkeypatch.setattr(routes, "served", state)
    monkeypatch.setattr(http, "served", state)
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app, client=("192.0.2.99", 50000)) as client:
        yield client, ids, now


def attempt(client, path, address, password, username="reader@example.com"):
    return client.post(path, headers={"x-caller-token": "portal-service", "x-client-address": address},
                       json={"username": username, "password": password})


@pytest.mark.parametrize("path", ["/session", "/session/token"])
def test_forwarded_signins_isolate_addresses_and_keep_the_existing_limits(context_identity, path):
    client, ids, now = context_identity
    for _ in range(5):
        failed = attempt(client, path, "192.0.2.1", "wrong")
        assert failed.status_code == 401 and "set-cookie" not in failed.headers
    assert failed.headers["retry-after"] == "300"
    signed = attempt(client, path, "192.0.2.2", "right")
    assert signed.status_code == 200, signed.text
    if path == "/session":
        assert signed.json()["id"] == "reader@example.com"
        assert ids.sessions.get(client.cookies.get("session")) == "reader@example.com"
    else:
        assert signed.json()["expires_in"] == 900 and signed.json()["token_type"] == "Bearer"
        assert ids.from_token(signed.json()["access_token"]) == "reader@example.com"
        assert "set-cookie" not in signed.headers
    for password in ("wrong", "right"):
        blocked = attempt(client, path, "192.0.2.1", password)
        assert blocked.status_code == 429 and blocked.headers["retry-after"] == "300"
    assert attempt(client, path, "192.0.2.1", "right", username="other@example.com").status_code == 200
    now[0] += 299
    blocked = attempt(client, path, "192.0.2.1", "right")
    assert blocked.status_code == 429 and blocked.headers["retry-after"] == "1"
    now[0] += 1
    assert attempt(client, path, "192.0.2.1", "right").status_code == 200


@pytest.mark.parametrize("first,second", [("/session", "/session/token"), ("/session/token", "/session")])
def test_switching_context_routes_or_username_spelling_never_evades_lockout(context_identity, first, second):
    client, _, _ = context_identity
    for _ in range(5):
        assert attempt(client, first, "192.0.2.1", "wrong", username=" ＲＥＡＤＥＲ@Example.com ").status_code == 401
    blocked = attempt(client, second, "192.0.2.1", "right")
    assert blocked.status_code == 429 and blocked.headers["retry-after"] == "300"
    assert attempt(client, second, "192.0.2.2", "right").status_code == 200
