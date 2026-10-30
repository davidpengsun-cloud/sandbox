"""A password-exchanged bearer reaches every mounted MCP capability on the running app."""
from fastapi.testclient import TestClient

from kge.services.identity import service
from tests.integration.test_signin import app_on, client, passwords
from tests.scratch import scratch_build


def test_password_bearer_authenticates_all_mcp_mounts_and_preserves_units(tmp_path, monkeypatch):
    build = scratch_build(tmp_path / "build")
    plain = passwords(build)
    app = app_on(build, monkeypatch)
    with client(app, https=True) as caller:
        reply = caller.post("/session/token", json={"username": "analyst_retail", "password": plain["analyst_retail"]})
        assert reply.status_code == 200, reply.text
        token = reply.json()["access_token"]
        auth = {"authorization": f"Bearer {token}", "accept": "application/json, text/event-stream", "host": "kge-meridian-abc-uc.a.run.app"}
        initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}}
        browser = TestClient(app.app, base_url="https://testserver", client=("203.0.113.8", 50000))
        signed = browser.post("/session", json={"username": "analyst_retail", "password": plain["analyst_retail"]})
        assert signed.status_code == 200 and browser.get("/whoami").status_code == 200
        cookie = "; ".join(f"{name}={value}" for name, value in browser.cookies.items())
        assert cookie
        for path in ("/mcp", "/mcp/answer", "/mcp/onboard", "/mcp/catalogue", "/mcp/data", "/mcp/identity", "/mcp/search"):
            assert caller.post(path, json=initialize, headers={"host": auth["host"]}).status_code == 401
            assert caller.post(path, json=initialize, headers={**auth, "authorization": "Bearer not-a-token"}).status_code == 401
            assert caller.post(path, json=initialize, headers={**auth, "authorization": "", "cookie": cookie}).status_code == 401
            response = caller.post(path, json=initialize, headers=auth)
            assert response.status_code == 200 and "result" in response.json(), response.text
        response = caller.post("/mcp/identity", headers=auth, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                                                 "params": {"name": "readable_units", "arguments": {}}})
        result = response.json()["result"]
        assert not result.get("isError") and result["structuredContent"] == {"units": ["retail"]}
        assert caller.get("/whoami", headers=auth).status_code == 200
        refused = caller.post("/session", json={"token": token})
        assert refused.status_code == 401 and "set-cookie" not in refused.headers
        assert caller.get("/whoami").status_code == 401
        now = service.time.time()
        monkeypatch.setattr(service.time, "time", lambda: now + 901)
        assert caller.post("/mcp/identity", json=initialize, headers=auth).status_code == 401
