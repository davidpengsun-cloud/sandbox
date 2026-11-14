"""The developer entry point uses the installation’s settings and working reference links."""
import json
import re
from html import unescape
from html.parser import HTMLParser

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kge.pipeline.settings import context_app_name, run_job
from kge.services.http import principal
from kge.services.tools import routes, service


class Links(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.urls = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.urls.extend(value for key, value in attrs if key in {"href", "src", "spec-url"})


@pytest.mark.parametrize("name, expected", [(None, "Context"), ("  ", "Context"), ("  Atlas  ", "Atlas")])
def test_context_app_name_is_separate_from_discovery(monkeypatch, name, expected):
    monkeypatch.setenv("PORTAL_URL", "https://portal.example.com/")
    monkeypatch.setenv("PRODUCT_NAME", "Beacon")
    if name is None:
        monkeypatch.delenv("CONTEXT_APP_NAME", raising=False)
    else:
        monkeypatch.setenv("CONTEXT_APP_NAME", name)
    assert context_app_name() == expected
    page = service.developer_page("https://api.example.com/", {"status": "ok", "version": "1.2.3"}, False)
    assert f"<title>{expected} for developers</title>" in page
    assert f"Build on {expected}" in page
    assert "Open Beacon" in page


def test_landing_uses_request_values_links_and_escaped_branding(monkeypatch):
    from kge import app as running

    monkeypatch.setenv("CLIENT_NAME", 'A & B <script>bad()</script>')
    monkeypatch.setenv("CONTEXT_APP_NAME", 'Context <script>bad()</script>')
    monkeypatch.setenv("PRODUCT_NAME", "Discovery")
    monkeypatch.setenv("PORTAL_URL", "https://portal.example.com/")
    monkeypatch.setenv("API_URL", "https://wrong.example.com")
    monkeypatch.setattr(running, "VERSION", "1.2.3")
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app, base_url="https://api.example.com")
    page = client.get("/")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    assert "https://api.example.com" in page.text and "wrong.example.com" not in page.text
    assert "1.2.3" in page.text and "Service healthy" in page.text and "15 minutes" in page.text
    assert "<script>bad()" not in page.text and "&lt;script&gt;bad()" in page.text
    content = re.sub(r"<style[^>]*>.*?</style>", "", page.text, flags=re.S)
    assert "KGE" not in content and "ALTA" not in content
    assert "font-family: 'Inter'" in page.text and "font-family: 'Geist Mono'" in page.text
    assert '<style id="bundled-fonts">' in page.text
    urls = Links(page.text).urls
    assert {"/docs", "/redoc", "/openapi.json", "/health", "/versions",
            "https://portal.example.com/home", "https://portal.example.com/api/context/onboarding-guide"} <= set(urls)
    for path in ("/docs", "/redoc", "/openapi.json", "/health"):
        assert client.get(path).status_code == 200
    version_route = next(route for route in routes.router.routes if getattr(route, "path", "") == "/versions")
    assert any(dependency.call is principal for dependency in version_route.dependant.dependencies)
    config = json.loads(unescape(re.search(r'<pre id="client-config"[^>]*>(.*?)</pre>', page.text, re.S)[1]))
    assert config["mcpServers"]["context"]["url"] == "https://api.example.com/mcp"
    assert config["mcpServers"]["context"]["headers"] == {"Authorization": "Bearer <access_token>"}
    assert {"api-address", "client-config"} == set(re.findall(r'data-copy="([^"]+)"', page.text))
    assert 'name="viewport"' in page.text and "@media(max-width:600px)" in page.text


@pytest.mark.parametrize("portal", [None, "", "  "])
def test_landing_uses_existing_logo_and_omits_unconfigured_portal_links(monkeypatch, portal):
    if portal is None:
        monkeypatch.delenv("PORTAL_URL", raising=False)
    else:
        monkeypatch.setenv("PORTAL_URL", portal)
    monkeypatch.delenv("PRODUCT_NAME", raising=False)
    monkeypatch.setenv("CLIENT_NAME", "Meridian")
    page = service.developer_page("http://testserver", {"status": "ok", "version": "dev"}, True)
    assert '<img src="/brand/logo" alt="Meridian"' in page
    assert "Open ALTA" not in page and "Context Docs" not in page
    assert not {"/context", "/home", "/api/context/onboarding-guide"} & set(Links(page).urls)
    assert not re.search(r"\[[A-Z_]+\]", page)


def test_job_setting_is_read_afresh_and_defaults_to_service_name(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "context-service")
    monkeypatch.delenv("RUN_JOB", raising=False)
    assert run_job() == "context-service"
    monkeypatch.setenv("RUN_JOB", "context-onboard")
    assert run_job() == "context-onboard"


def test_context_developer_guide_links_to_both_references():
    class People:
        def people(self):
            return {"developer@example.com"}

        def role_of_person(self, who, roles):
            return {}

    page = service.onboarding_guide("developer@example.com", People(), {}, "developer", api_url="http://testserver")
    assert '<a href="/docs"' in page and '<a href="/redoc"' in page


def test_token_instructions_use_the_bearer_endpoint_and_shared_lifetime(monkeypatch):
    from kge.services.identity import SignedBearer

    monkeypatch.setattr(SignedBearer, "SECONDS", 1200)
    page = service.developer_page("http://testserver", {"status": "ok", "version": "dev"}, False)
    token = unescape(page.split('<section id="token"', 1)[1].split("</section>", 1)[0])
    assert "POST /session/token</code>" in token and "POST /session</code>" not in token
    assert '{"username": "…", "password": "…"}' in token
    assert "response’s" in token and "access_token</code>" in token
    assert "Authorization: Bearer <access_token>" in token
    assert "20 minutes" in token
