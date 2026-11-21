"""Access HTTP and MCP use the same identity, admin authority and audited capabilities."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kge.adapters import appstate
from kge.pipeline import access_table
from kge.services import http
from kge.services.access import mcp, routes
from kge.services.ask.mcp import MCPEndpoint
from kge.services.identity import Identities, token_for
from tests.unit.test_access_table import ADMIN, prof as prof

PERSON = "person@example.com"
OPERATOR = "operator@example.com"


@pytest.fixture
def api(prof, tmp_path, monkeypatch):
    ids = Identities(tmp_path / "tokens", [ADMIN, PERSON, OPERATOR])
    state = SimpleNamespace(PROFILE=prof, ids=lambda: ids, COOKIE="session", SIGN_IN="sign in", UNRELEASED_MESSAGE=None)
    monkeypatch.setattr(http, "served", state)
    monkeypatch.setattr(routes, "served", state)
    server = mcp.mcp_server()

    @asynccontextmanager
    async def lifespan(app):
        async with server.session_manager.run():
            yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(routes.router)
    app.add_route("/mcp/access", MCPEndpoint(server), methods=["GET", "POST", "DELETE"])
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, ids=ids, server=server,
                              headers=lambda who: {"Authorization": "Bearer " + token_for(ids.folder, who)})


def tool(api, name, arguments=None, who=ADMIN, headers=None):
    response = api.client.post("/mcp/access", headers={"Accept": "application/json, text/event-stream",
        **(api.headers(who) if headers is None else headers)}, json={"jsonrpc": "2.0", "id": 1,
        "method": "tools/call", "params": {"name": name, "arguments": arguments or {}}})
    return response


def result(response):
    assert response.status_code == 200, response.text
    return response.json()["result"]


@pytest.mark.parametrize("method,path,body", [("GET", "/access", None), ("GET", "/access/audit", None),
    ("PUT", "/access/person@example.com", {"roles": ["reader"], "name": "Person"}),
    ("DELETE", "/access/person@example.com", None)])
def test_every_http_capability_requires_an_admin(api, prof, method, path, body):
    assert api.client.request(method, path, json=body).status_code == 401
    response = api.client.request(method, path, json=body, headers=api.headers(PERSON))
    assert response.status_code == 403
    assert "not an admin" in response.json()["detail"]
    assert appstate.access_state(prof).access_audit() == []


@pytest.mark.parametrize("name,args", [("list_access", {}), ("access_audit", {}),
    ("grant_access", {"email": PERSON, "roles": ["reader"]}), ("revoke_access", {"email": PERSON})])
def test_every_tool_checks_the_same_admin_before_arguments(api, prof, name, args):
    refusal = result(tool(api, name, {**args, "principal": ADMIN}, who=PERSON))
    assert refusal["isError"] and "403:" in refusal["content"][0]["text"]
    assert appstate.access_state(prof).access_audit() == []
    assert api.server._tool_manager.checks[name] is http.admin


def test_grant_audit_list_and_revoke_cross_transports(api, prof):
    response = api.client.put(f"/access/{PERSON}", headers=api.headers(ADMIN),
                              json={"roles": ["reader", "keeper"], "name": "Person"})
    assert response.status_code == 200
    entry = response.json()
    assert entry["before"] is None and entry["by"] == ADMIN and entry["via"] == "api"
    assert entry["after"]["roles"] == ["keeper", "reader"] and not entry["changed_outside"]
    listing = result(tool(api, "list_access"))["structuredContent"]
    assert listing == api.client.get("/access", headers=api.headers(ADMIN)).json()
    assert listing["people"][0]["name"] == "Person" and ADMIN in listing["admins"]
    assert {role["id"] for role in listing["roles"]} == {"reader", "keeper", "admin"}
    deleted = result(tool(api, "revoke_access", {"email": PERSON}))["structuredContent"]
    assert deleted["before"]["name"] == "Person" and deleted["after"] is None
    audit = api.client.get("/access/audit?limit=1", headers=api.headers(ADMIN)).json()
    assert audit == result(tool(api, "access_audit", {"limit": 1}))["structuredContent"]
    assert len(audit["entries"]) == 1 and audit["entries"][0]["sequence"] == 2


@pytest.mark.parametrize("username", ["division/person@example.com", "audit/person@example.com", "/person@example.com"])
def test_encoded_slash_usernames_share_one_row_across_transports(api, prof, username):
    path = "/access/" + quote(username, safe="")
    response = api.client.put(path, headers=api.headers(ADMIN), json={"roles": ["reader"]})
    assert response.status_code == 200, response.text
    assert response.json()["username"] == username
    assert access_table.Access.for_profile(prof).roles_of(username) == {"reader"}
    updated = result(tool(api, "grant_access", {"email": username, "roles": ["keeper"]}))["structuredContent"]
    assert updated["before"]["roles"] == ["reader"] and updated["username"] == username
    assert api.client.delete(path, headers=api.headers(PERSON)).status_code == 403
    deleted = api.client.delete(path, headers=api.headers(ADMIN))
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["before"]["roles"] == ["keeper"] and deleted.json()["after"] is None
    assert not access_table.Access.for_profile(prof).roles_of(username)
    audit = api.client.get("/access/audit", headers=api.headers(ADMIN)).json()["entries"]
    assert len(audit) == 3 and all(entry["username"] == username for entry in audit)


@pytest.mark.parametrize("roles", [["unknown"], ["access_admin"], []])
def test_invalid_grants_refuse_without_audit_on_both_transports(api, prof, roles):
    response = api.client.put(f"/access/{PERSON}", headers=api.headers(ADMIN), json={"roles": roles})
    assert response.status_code == 422
    refusal = result(tool(api, "grant_access", {"email": PERSON, "roles": roles}))
    assert refusal["isError"] and "422:" in refusal["content"][0]["text"]
    assert appstate.access_state(prof).access_audit() == []


def test_actor_is_never_taken_from_body_or_tool_arguments(api):
    body = {"roles": ["reader"], "principal": ADMIN}
    assert api.client.put(f"/access/{PERSON}", headers=api.headers(ADMIN), json=body).status_code == 422
    assert "422:" in result(tool(api, "grant_access", {"email": PERSON, **body}))["content"][0]["text"]
    cookie = api.ids.sessions.open(ADMIN)
    api.client.cookies.set("session", cookie)
    assert api.client.get("/access").status_code == 200
    assert tool(api, "list_access", headers={}).status_code == 401


def test_stored_admin_changes_take_effect_and_ordinary_revoke_preserves_admin(api, prof):
    access_table.set_admins(prof, OPERATOR, by=OPERATOR)
    assert api.client.get("/access", headers=api.headers(OPERATOR)).status_code == 200
    assert not result(tool(api, "grant_access", {"email": OPERATOR, "roles": ["reader"]}, who=OPERATOR)).get("isError")
    assert api.client.delete(f"/access/{OPERATOR}", headers=api.headers(OPERATOR)).status_code == 200
    assert access_table.Access.for_profile(prof).roles_of(OPERATOR) == {"access_admin"}
    access_table.set_admins(prof, "", by=OPERATOR)
    assert api.client.get("/access", headers=api.headers(OPERATOR)).status_code == 403
    assert "403:" in result(tool(api, "list_access", who=OPERATOR))["content"][0]["text"]


def test_outside_changes_are_reported_by_both_transports(api, prof, monkeypatch, caplog):
    monkeypatch.setattr(access_table, "changed_outside", lambda prof: True)
    assert api.client.get("/access", headers=api.headers(ADMIN)).json()["changed_outside"]
    assert result(tool(api, "access_audit"))["structuredContent"]["changed_outside"]
    entry = result(tool(api, "grant_access", {"email": PERSON, "roles": ["reader"]}))["structuredContent"]
    assert entry["changed_outside"] and "changed outside KGE" in caplog.text


def test_audit_limits_and_absent_revoke_match(api):
    for limit in (0, 1001):
        assert api.client.get(f"/access/audit?limit={limit}", headers=api.headers(ADMIN)).status_code == 422
        assert "422:" in result(tool(api, "access_audit", {"limit": limit}))["content"][0]["text"]
    assert api.client.delete(f"/access/{PERSON}", headers=api.headers(ADMIN)).status_code == 404
    assert "404:" in result(tool(api, "revoke_access", {"email": PERSON}))["content"][0]["text"]


@pytest.mark.parametrize("error,status", [(appstate.StateConflict("reload"), 409),
                                         (appstate.StateUnavailable("unavailable"), 503)])
def test_write_failures_are_transport_errors(api, monkeypatch, error, status):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(access_table, "grant", fail)
    assert api.client.put(f"/access/{PERSON}", headers=api.headers(ADMIN), json={"roles": ["reader"]}).status_code == status
    assert f"{status}:" in result(tool(api, "grant_access", {"email": PERSON, "roles": ["reader"]}))["content"][0]["text"]



def test_access_can_be_inspected_before_first_release(api, prof):
    (prof.artifacts / prof.client / "current").unlink()
    access_table.set_admins(prof, [OPERATOR], by=OPERATOR)
    response = api.client.get("/access", headers=api.headers(ADMIN))
    assert response.status_code == 200
    assert {role["id"] for role in response.json()["roles"]} == {"reader", "keeper", "admin"}
    assert OPERATOR in response.json()["admins"]
    assert result(tool(api, "list_access"))["structuredContent"] == response.json()
    assert api.client.get("/access/audit", headers=api.headers(ADMIN)).json()["entries"]


def test_grants_before_first_release_share_http_and_mcp_rules(api, prof):
    (prof.artifacts / prof.client / "current").unlink()
    response = api.client.put(f"/access/{PERSON}", headers=api.headers(ADMIN),
                              json={"roles": ["reader"], "name": "Person"})
    assert response.status_code == 200
    updated = result(tool(api, "grant_access", {"email": PERSON, "roles": ["keeper"]}))["structuredContent"]
    assert updated["before"]["roles"] == ["reader"]
    assert updated["after"]["roles"] == ["keeper"]
    for role in ("unknown", "access_admin"):
        refused = api.client.put(f"/access/{PERSON}", headers=api.headers(ADMIN), json={"roles": [role]})
        assert refused.status_code == 422
        assert result(tool(api, "grant_access", {"email": PERSON, "roles": [role]}))["isError"]
