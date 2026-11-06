"""The portal owns Context; the standalone page remains an explicit fallback."""
import re
from html import escape

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kge.pipeline.settings import ROOT, context_page_enabled, check_service_auth_settings, product_name
from kge.services.tools import routes, service



@pytest.mark.parametrize("value, product", [(None, "Meridian ALTA"), ("", "Meridian ALTA"), (" \t ", "Meridian ALTA"),
                                            ("  Beacon  ", "Meridian Beacon")])
def test_product_name_takes_the_short_name_from_product_name_trimmed_with_alta_by_default(monkeypatch, value, product):
    """PRODUCT_NAME, supplied from the portal's PORTAL_PRODUCT_NAME, names KGE's pages after the client's name."""
    monkeypatch.setenv("CLIENT_NAME", "Meridian")
    if value is None:
        monkeypatch.delenv("PRODUCT_NAME", raising=False)
    else:
        monkeypatch.setenv("PRODUCT_NAME", value)
    assert product_name() == product
    monkeypatch.setenv("CONTEXT_PAGE", "on")
    assert f"<title>{escape(product)} — Context</title>" in service.context_page()

@pytest.mark.parametrize("value, expected", [(None, False), ("off", False), ("on", True)])
@pytest.mark.parametrize("name, product", [(None, "Client ALTA"), ("", "Client ALTA"),
                                           (" \t ", "Client ALTA"), ("  Meridian Group  ", "Meridian Group ALTA"),
                                           ('A & B </title><script>alert("name")</script>',
                                            'A & B </title><script>alert("name")</script> ALTA')])
def test_page_switch_leaves_the_api_and_guide_available(monkeypatch, value, expected, name, product):
    monkeypatch.delenv("PRODUCT_NAME", raising=False)
    if name is None:
        monkeypatch.delenv("CLIENT_NAME", raising=False)
    else:
        monkeypatch.setenv("CLIENT_NAME", name)
    monkeypatch.setenv("PROFILE", "a-different-profile")
    assert product_name() == product
    if value is None:
        monkeypatch.delenv("CONTEXT_PAGE", raising=False)
    else:
        monkeypatch.setenv("CONTEXT_PAGE", value)
    monkeypatch.setattr(routes, "principal", lambda _: (_ for _ in ()).throw(routes.HTTPException(401)))
    app = FastAPI(title=product_name())
    app.include_router(routes.router)
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == (200 if expected else 404)
        if expected:
            assert f"<title>{escape(product)} — Context</title>" in page.text
            assert "[Client name]" not in page.text
            assert page.headers["cache-control"] == "no-store"
            assert 'id="nav-tools"' not in page.text
            assert 'id="sql-check"' not in page.text
            assert 'mcpServers:{context:' not in page.text
        assert client.get("/health").json() == {"status": "ok", "version": "dev"}
        guide = client.get("/onboarding-guide")
        assert guide.status_code == 200
        assert "Sign in and connect" in guide.text
        assert f"<title>{escape(product)} — Docs</title>" in guide.text
        assert "[Client name]" not in guide.text
        assert not re.search(r"\bKGE\b", guide.text)
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").json()["info"]["title"] == product
    assert context_page_enabled() is expected


@pytest.mark.parametrize("value", ["", "true", "false", "ON", "1", "of"])
def test_unknown_page_setting_refuses_startup(monkeypatch, value):
    monkeypatch.setenv("CONTEXT_PAGE", value)
    with pytest.raises(ValueError, match="CONTEXT_PAGE must be on or off"):
        check_service_auth_settings()


def test_guide_home_reaches_the_portal_without_a_fallback(monkeypatch):
    monkeypatch.setenv("CONTEXT_PAGE", "off")
    monkeypatch.setenv("PORTAL_URL", "https://portal.example.com/")
    assert 'href="https://portal.example.com/context" target="_top"' in service.onboarding_guide()
    monkeypatch.setenv("CONTEXT_PAGE", "on")
    assert 'href="/" target="_top"' in service.onboarding_guide()


def test_end_user_guides_contain_no_deployment_steps():
    for path in (ROOT / "ui/guides").glob("*.md"):
        text = path.read_text()
        assert not re.search(r"\bKGE\b", text), path
        assert "CONTEXT_PAGE" not in text and "fallback" not in text, path
