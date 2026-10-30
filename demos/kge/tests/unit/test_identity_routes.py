"""The roster and demo sign-in share a loopback boundary, including the browser's Host."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from kge.services import http
from kge.services.identity import routes
from kge.services.tools import routes as tools


@pytest.fixture
def identity_app(monkeypatch):
    roster = [{"id": "reader", "role": "Reader", "reads": ["sample"], "processes": ["querying"]}]
    ids = SimpleNamespace(resolve=lambda auth, cookie: "reader" if auth == "Bearer valid" or cookie == "session" else None,
                          demo_session=lambda who: ("session", who) if who == "reader" else None)
    graph = SimpleNamespace(principals=lambda: roster, principal=lambda who: roster[0])
    state = SimpleNamespace(DEMO_MODE=False, PASSWORDS=True, LOOPBACK={"127.0.0.1", "::1"}, COOKIE="session",
                            SIGN_IN="sign in", ids=lambda: ids, engine=lambda: SimpleNamespace(g=graph))
    for module in (http, routes, tools):
        monkeypatch.setattr(module, "served", state)
    app = FastAPI()
    app.include_router(routes.router)
    app.include_router(tools.router)
    return app, state, roster


@pytest.mark.parametrize("passwords", [True, False])
def test_roster_requires_sign_in_outside_demo(identity_app, passwords):
    app, state, roster = identity_app
    state.PASSWORDS = passwords
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
        assert client.get("/principals").status_code == 401
        assert client.get("/principals", headers={"Authorization": "Bearer invalid"}).status_code == 401
        assert client.get("/principals", headers={"Authorization": "Bearer valid"}).json() == roster
        client.cookies.set("session", "session")
        assert client.get("/principals").json() == roster


@pytest.mark.parametrize("address", ["127.0.0.1", "::1"])
@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.1:8000", "localhost", "LOCALHOST:80", "[::1]", "[::1]:8000"])
def test_demo_loopback_lists_and_signs_in(identity_app, address, host):
    app, state, roster = identity_app
    state.DEMO_MODE = True
    with TestClient(app, client=(address, 50000)) as client:
        headers = {"Host": host}
        assert client.get("/mode", headers=headers).json()["demo_mode"] is True
        assert client.get("/principals", headers=headers).json() == roster
        result = client.post("/session/demo", headers=headers, json={"principal": "reader"})
        assert result.status_code == 200 and result.json()["id"] == "reader"


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8000", "localhost.evil.example", "127.0.0.1.evil.example",
                                  "localhost@evil.example", "evil.example@localhost", "localhost/path", "localhost:bad",
                                  "[::1].evil.example", "127.0.0.2", "::1", "localhost,evil.example"])
def test_demo_refuses_rebinding_host_for_every_loopback_privilege(identity_app, host):
    app, state, _ = identity_app
    state.DEMO_MODE = True
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        headers = {"Host": host, "Origin": "https://evil.example", "X-Forwarded-Host": "localhost"}
        result = client.post("/session/demo", headers=headers, json={"principal": "reader"})
        assert result.status_code == 404 and "set-cookie" not in result.headers
        assert client.get("/mode", headers=headers).json()["demo_mode"] is False
        assert client.get("/principals", headers=headers).status_code == 401
        assert client.get("/principals", headers={**headers, "Authorization": "Bearer valid"}).status_code == 200


def test_loopback_host_never_authorises_a_remote_client(identity_app):
    app, state, _ = identity_app
    state.DEMO_MODE = True
    with TestClient(app, base_url="http://localhost", client=("203.0.113.7", 50000)) as client:
        assert client.get("/mode").json()["demo_mode"] is False
        assert client.get("/principals").status_code == 401
        assert client.post("/session/demo", json={"principal": "reader"}).status_code == 404


@pytest.mark.parametrize("headers", [[], [(b"host", b"localhost"), (b"host", b"evil.example")],
                                   [(b"host", b"evil.example"), (b"host", b"localhost")]])
def test_demo_refuses_missing_or_ambiguous_host(identity_app, headers):
    _, state, _ = identity_app
    state.DEMO_MODE = True
    assert not http.demo_allowed(Request({"type": "http", "headers": headers, "client": ("127.0.0.1", 50000)}))
