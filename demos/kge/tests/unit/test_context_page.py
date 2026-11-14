"""Context lives in the portal; the service root is always the developer page."""
import re
from html import escape
from types import SimpleNamespace

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kge.pipeline.settings import ENVIRONMENT_NAMES, ROOT, Settings, product_name
from kge.services.tools import routes, service


def test_context_setting_and_standalone_page_are_removed():
    assert "CONTEXT_PAGE" not in ENVIRONMENT_NAMES
    assert "CONTEXT_PAGE" not in Settings.__dataclass_fields__
    assert not (ROOT / "ui/index.html").exists()
    assert not hasattr(service, "context_page")


def test_root_always_serves_the_developer_page_and_public_routes_remain(monkeypatch):
    monkeypatch.setattr(routes, "served", SimpleNamespace(BRAND=SimpleNamespace(logo=None), VERSION="dev", runtimes_health=lambda: None))
    monkeypatch.setenv("PORTAL_URL", "https://portal.example.com")
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert page.headers["cache-control"] == "no-store"
        assert "Context for developers" in page.text
        assert 'id="bundled-fonts"' in page.text
        assert 'id="nav-ask"' not in page.text
        assert client.get("/health").status_code == 200
        assert client.get("/onboarding-guide").status_code == 200
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200


def test_guide_home_reaches_the_portal(monkeypatch):
    monkeypatch.setenv("PORTAL_URL", "https://portal.example.com/")
    assert 'href="https://portal.example.com/context" target="_top"' in service.onboarding_guide()


@pytest.mark.parametrize("name, expected", [(None, "Meridian ALTA"), ("", "Meridian ALTA"), (" \t ", "Meridian ALTA"), ("  Beacon  ", "Meridian Beacon")])
def test_product_name_still_names_guides_and_api_independently_of_context(monkeypatch, name, expected):
    monkeypatch.setenv("CLIENT_NAME", "Meridian")
    if name is None:
        monkeypatch.delenv("PRODUCT_NAME", raising=False)
    else:
        monkeypatch.setenv("PRODUCT_NAME", name)
    assert product_name() == expected
    assert f"<title>{escape(expected)} — Docs</title>" in service.onboarding_guide()


def test_end_user_guides_contain_no_deployment_steps():
    for path in (ROOT / "ui/guides").glob("*.md"):
        text = path.read_text()
        assert not re.search(r"\bKGE\b", text), path
        assert "CONTEXT_PAGE" not in text and "fallback" not in text, path
