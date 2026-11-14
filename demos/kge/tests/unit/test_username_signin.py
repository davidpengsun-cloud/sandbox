"""The shared username rules apply before both password sign-in capabilities."""
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kge.services import http
from kge.services.identity import routes, service


@pytest.fixture
def login(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-key-" * 3)
    monkeypatch.setenv("SIGNIN_PASSWORDS", json.dumps({"strasse@example.com": service.hash_password("right"),
                                                       "x" * 254: service.hash_password("right")}))
    known = {"strasse@example.com", "x" * 254}
    ids = service.Identities(tmp_path, lambda: known, service.SignedSessions(tmp_path, "test"))
    graph = SimpleNamespace(principal=lambda who: {"id": who}, processes=lambda who: {"querying"})
    served = SimpleNamespace(PASSWORDS=True, THROTTLE=service.Throttle(), ids=lambda: ids,
                             engine=lambda: SimpleNamespace(g=graph), COOKIE="session", SIGN_IN="sign in",
                             TRUSTED_CALLER=None)
    monkeypatch.setattr(routes, "served", served)
    monkeypatch.setattr(http, "served", served)
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        yield client, ids


@pytest.mark.parametrize("route", ["/session", "/session/token"])
@pytest.mark.parametrize("supplied,expected", [(" Ｓｔｒａße@EXAMPLE.COM ", "strasse@example.com"),
                                               (" " + "x" * 254 + " ", "x" * 254)])
def test_both_signins_return_canonical_identity(login, route, supplied, expected):
    client, ids = login
    result = client.post(route, json={"username": supplied, "password": "right"})
    assert result.status_code == 200, result.text
    if route == "/session":
        assert result.json()["id"] == expected
        assert client.get("/whoami").json()["id"] == expected
    else:
        token = result.json()["access_token"]
        assert ids.from_token(token) == expected
        assert client.get("/whoami", headers={"Authorization": f"Bearer {token}"}).json()["id"] == expected


@pytest.mark.parametrize("route", ["/session", "/session/token"])
@pytest.mark.parametrize("username", ["strasse@example.com\n", "strasse\u200b@example.com", "access_admin",
                                      "ＡＣＣＥＳＳ_ＡＤＭＩＮ", "ROLE:reader", "a b", "x" * 255, ""])
def test_invalid_unknown_and_existing_names_have_identical_failures(login, route, username):
    client, _ = login
    responses = [client.post(route, json={"username": who, "password": "wrong"})
                 for who in (username, "missing@example.com", "strasse@example.com")]
    assert all(response.status_code == 401 for response in responses)
    assert all(response.json() == {"detail": "wrong username or password"} for response in responses)
    assert all("retry-after" not in response.headers and "set-cookie" not in response.headers for response in responses)


@pytest.mark.parametrize("route", ["/session", "/session/token"])
def test_control_character_alias_cannot_sign_in_with_correct_password(login, route):
    client, _ = login
    result = client.post(route, json={"username": "strasse@example.com\n", "password": "right"})
    assert result.status_code == 401
    assert result.json() == {"detail": "wrong username or password"}


@pytest.mark.parametrize("bootstrap", [False, True])
def test_whoami_displays_access_admin_without_changing_role_ids(login, monkeypatch, bootstrap):
    client, ids = login
    who = "access_admin.sample@example.com"
    roles = set() if bootstrap else {"access_admin"}
    ids.access = SimpleNamespace(name_of=lambda _: "Access operator", roles_of=lambda _: roles,
                                 is_admin=lambda _: True)
    ids.people = lambda: {who}
    monkeypatch.setattr(routes.served.engine().g, "principal", lambda _: {"role": ""})
    token = ids.token_for(who)
    result = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert result.status_code == 200
    assert result.json()["role"] == "Access admin"
    assert result.json()["roles"] == sorted(roles)
    assert result.json()["id"] == who
