"""An empty installation stays live and gives HTTP and MCP the same release-first refusal."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from kge.pipeline.profile import ProfileError, resolve


@pytest.fixture
def unreleased(monkeypatch, tmp_path):
    from kge import app
    prof = replace(resolve(), artifacts=tmp_path / "artifacts", store=tmp_path / "store",
                   pinned=None)
    monkeypatch.setattr(app, "PROFILE", prof.serve())
    monkeypatch.setattr(app.app, "middleware_stack", None)
    for name in ("_engine", "_combined", "_agent", "_ids", "UNRELEASED_MESSAGE"):
        monkeypatch.setattr(app, name, None)
    monkeypatch.setattr(app, "BRAND", SimpleNamespace(start=lambda: None, stop=lambda: None, logo=None))
    return app


@pytest.mark.parametrize("page", ["off", "on"])
def test_empty_startup_serves_only_public_routes_and_logs_one_line(unreleased, caplog, monkeypatch, page):
    monkeypatch.setenv("CONTEXT_PAGE", page)
    app = unreleased
    message = f"nothing released for {app.PROFILE.client}: release first"

    async def check():
        async with app.lifespan(app.app):
            assert app._engine is app._ids is None
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.app), base_url="http://localhost") as client:
                for path in ("/health", "/healthz", "/onboarding-guide", "/mode"):
                    response = await client.get(path)
                    assert response.status_code == 200, (path, response.text)
                assert (await client.get("/")).status_code == (200 if page == "on" else 404)
                for path in ("/health", "/healthz"):
                    assert (await client.head(path)).status_code == 200
                    data = (await client.get(path)).json()
                    assert data == {"status": "ok", "version": app.VERSION}
                assert (await client.get("/brand/logo")).status_code == 404
                assert (await client.get("/brand/logo.svg")).status_code == 404
                mode = (await client.get("/mode")).json()
                assert mode["released"] is False and mode["message"] == message
                for method, path in (("GET", "/profile"), ("GET", "/onboard"), ("GET", "/docs"),
                                     ("GET", "/openapi.json"), ("GET", "/unknown"), ("POST", "/ask"),
                                     ("POST", "/session"), ("POST", "/onboard/run"), ("POST", "/health"),
                                     ("DELETE", "/mode"), ("POST", "/mcp"), ("POST", "/mcp/onboard"),
                                     ("POST", "/mcp/answer"), ("GET", "/mcp/data")):
                    response = await client.request(method, path, content=b"not JSON", headers={"Authorization": "Bearer invalid"})
                    assert response.status_code == 503, (method, path, response.text)
                    assert response.json() == {"detail": message}
                assert app._engine is app._ids is None
        assert not (app.PROFILE.run_dir() / "api.pid").exists()

    asyncio.run(check())
    records = [record for record in caplog.records if record.name == "kge.app"]
    assert [record.getMessage() for record in records] == [message]
    assert not records[0].exc_info


def test_mcp_uses_the_shared_release_first_error(unreleased):
    from kge.services.http import mcp_subject, mcp_server
    app = unreleased

    async def check():
        async with app.lifespan(app.app):
            message = f"503: nothing released for {app.PROFILE.client}: release first"
            with pytest.raises(ToolError, match=message):
                mcp_subject(None)
            server = mcp_server("unreleased-test")

            @server.tool()
            def never_called() -> str:
                raise AssertionError("an unreleased tool must never run")

            with pytest.raises(ToolError, match=message):
                await server._tool_manager.call_tool("never_called", {})

    asyncio.run(check())


def test_a_named_release_profile_error_is_not_hidden(unreleased, monkeypatch):
    app = unreleased
    monkeypatch.setattr(app, "PROFILE", app.PROFILE.at("a" * 64))

    def broken(profile):
        raise ProfileError("a named release is damaged")

    monkeypatch.setattr(app.Engine, "serving", broken)
    with pytest.raises(ProfileError, match="named release is damaged"):
        app.engine()
    assert app.UNRELEASED_MESSAGE is None


def test_mode_of_a_serving_app_is_released(unreleased):
    from starlette.requests import Request
    from kge.services.tools.routes import mode
    result = mode(Request({"type": "http", "headers": [], "client": ("127.0.0.1", 8000)}))
    assert result["released"] is True and result["message"] is None


@pytest.mark.parametrize("cloud", [False, True])
def test_first_release_is_served_without_restart(unreleased, monkeypatch, cloud):
    from kge.services.onboard import jobs
    from kge.services.tools import service
    app = unreleased
    clock = [100.0]
    published = [False]
    local_ready = [False]
    fetched = []
    opened = []
    monkeypatch.setattr(app, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(jobs, "_cloud", lambda: cloud)
    monkeypatch.setattr(jobs, "the_store", lambda: object())
    monkeypatch.setattr(service, "summary", lambda profile: {"hash": profile.model_hash()})

    def fetch(*args):
        fetched.append(True)
        if not published[0]:
            raise jobs.Refused("nothing released")
        local_ready[0] = True

    monkeypatch.setattr(jobs, "fetch", fetch)

    async def check():
        async with app.lifespan(app.app):
            prof = SimpleNamespace(model_hash=lambda: "a" * 64 if local_ready[0] else None)
            monkeypatch.setattr(app, "PROFILE", prof)
            class FreshEngine:
                def close(self):
                    pass
            fresh = FreshEngine()

            def serving(profile):
                assert profile.model_hash() == "a" * 64
                opened.append(True)
                return fresh

            monkeypatch.setattr(app.Engine, "serving", serving)
            monkeypatch.setattr(app, "Combined", lambda e: SimpleNamespace(e=e))
            monkeypatch.setattr(app, "Agent", lambda e: SimpleNamespace(e=e))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.app), base_url="http://localhost") as client:
                assert (await client.get("/summary")).status_code == 503
                published[0] = True
                local_ready[0] = not cloud
                assert (await client.get("/summary")).status_code == 503
                assert not opened
                assert len(fetched) == int(cloud)
                clock[0] += 3
                response = await client.get("/summary")
                assert response.status_code == 200 and response.json() == {"hash": "a" * 64}
                assert app._engine is fresh and app.UNRELEASED_MESSAGE is None
                for path in ("/health", "/healthz"):
                    data = (await client.get(path)).json()
                    assert data == {"status": "ok", "version": app.VERSION}
                mode = (await client.get("/mode")).json()
                assert mode["released"] is True and mode["message"] is None
                assert len(opened) == 1 and len(fetched) == 2 * int(cloud)
            monkeypatch.setattr(app, "PROFILE", original)

    original = app.PROFILE
    asyncio.run(check())


def test_first_release_fetch_leaves_health_responsive(unreleased, monkeypatch):
    import threading
    from kge.services.onboard import jobs
    app = unreleased
    entered = threading.Event()
    finish = threading.Event()
    fetched = []
    monkeypatch.setattr(jobs, "_cloud", lambda: True)
    monkeypatch.setattr(jobs, "the_store", lambda: object())

    def fetch(*args):
        fetched.append(True)
        entered.set()
        assert finish.wait(5), "the health request stalled behind the fetch"
        raise jobs.Refused("nothing released")

    monkeypatch.setattr(jobs, "fetch", fetch)

    async def check():
        async with app.lifespan(app.app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.app), base_url="http://localhost") as client:
                pending = asyncio.create_task(client.get("/summary"))
                try:
                    async with asyncio.timeout(2):
                        while not entered.is_set():
                            await asyncio.sleep(0.01)
                        response = await client.get("/health")
                        assert response.status_code == 200
                        assert response.json() == {"status": "ok", "version": app.VERSION}
                        assert (await client.get("/mode")).json()["released"] is False
                        assert len(fetched) == 1
                finally:
                    finish.set()
                    response = await pending
                assert response.status_code == 503

    asyncio.run(check())


@pytest.mark.parametrize("failure", ["empty", "engine", "store", "os"])
def test_first_release_rechecks_handle_failures(unreleased, monkeypatch, caplog, failure):
    from kge.services.onboard import jobs
    app = unreleased
    clock = [100.0]
    fetched = []
    opened = []
    monkeypatch.setattr(app, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(jobs, "_cloud", lambda: True)
    monkeypatch.setattr(jobs, "the_store", lambda: object())

    def fetch(*args):
        fetched.append(True)
        errors = {"empty": jobs.Refused, "store": jobs.StoreError, "os": OSError}
        if failure in errors:
            raise errors[failure]("nothing released" if failure == "empty" else "unavailable")

    def serving(profile):
        opened.append(True)
        raise RuntimeError("graph unavailable")

    monkeypatch.setattr(jobs, "fetch", fetch)

    async def check():
        async with app.lifespan(app.app):
            original = app.PROFILE
            if failure == "engine":
                monkeypatch.setattr(app, "PROFILE", SimpleNamespace(model_hash=lambda: "a" * 64))
                monkeypatch.setattr(app.Engine, "serving", serving)
            caplog.clear()
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.app), base_url="http://localhost") as client:
                    for _ in range(3):
                        response = await client.get("/summary")
                        assert response.status_code == 503
                        assert "release first" in response.json()["detail"]
                        assert (await client.get("/health")).status_code == 200
                        clock[0] += 3
                assert len(fetched) == 3
                assert len(opened) == (3 if failure == "engine" else 0)
                assert app._engine is None
            finally:
                monkeypatch.setattr(app, "PROFILE", original)

    asyncio.run(check())
    if failure == "empty":
        assert not caplog.records
    else:
        assert len(caplog.records) == 3
        assert all(record.levelname == "WARNING" and not record.exc_info for record in caplog.records)
        assert all("load the released graph" in record.getMessage() for record in caplog.records)
