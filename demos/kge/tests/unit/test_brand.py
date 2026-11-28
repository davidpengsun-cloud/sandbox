"""The client's logo and the portal's address for the shared bar: KGE_LOGO checked as the portal checks PORTAL_LOGO, the
portal's own logo taken only when it was configured with one, and neither giving the page its text fallback."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kge.adapters import brand
from kge.adapters.brand import Brand, BrandSettingError, Logo, configured_logo, logo_type, portal_address, portal_logo
from kge.services.tools import routes as tools_routes

PROBES = json.loads((Path(__file__).resolve().parents[2] / "portal/server/test/fixtures/logo-probes.json").read_text())

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xd9"
ICO = b"\x00\x00\x01\x00\x01\x00"
SVG = b'<?xml version="1.0"?>\n<!-- a mark -->\n<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><defs><g id="a"/></defs><use href="#a"/></svg>'


@pytest.mark.parametrize("content, media_type", [(PNG, "image/png"), (JPEG, "image/jpeg"), (ICO, "image/x-icon"),
                                                 (SVG, "image/svg+xml"), (b"\xef\xbb\xbf<svg/>", "image/svg+xml")])
def test_a_logo_s_type_is_read_from_its_content(content, media_type):
    assert logo_type(content) == media_type


@pytest.mark.parametrize("content", [
    b"", b"GIF89a", b"<html><svg/></html>", b"\xff\xfe<svg/>",
    b'<svg><script>alert(1)</script></svg>', b'<svg onload="alert(1)"/>', b'<svg><a href="https://example.com"/></svg>',
    b'<svg><image xlink:href="data:image/png;base64,AA"/></svg>', b'<svg><foreignObject/></svg>',
    b'<!DOCTYPE svg [<!ENTITY x "y">]><svg/>', PNG + b"\x00" * brand.LOGO_LIMIT,
])
def test_a_logo_that_is_no_image_or_could_run_or_fetch_anything_is_refused(content):
    assert logo_type(content) is None


def test_kge_logo_is_checked_once_and_a_bad_one_names_the_setting(tmp_path):
    assert configured_logo(None) is None and configured_logo("  ") is None
    good = tmp_path / "logo.svg"
    good.write_bytes(SVG)
    assert configured_logo(str(good)) == Logo(SVG, "image/svg+xml")
    png = tmp_path / "logo.PNG"
    png.write_bytes(PNG)
    assert configured_logo(str(png)).media_type == "image/png"
    for name, content in (("missing.svg", None), ("logo.gif", PNG), ("logo.svg", b"<svg onload='x()'/>"), ("raster.svg", PNG), ("vector.png", SVG),
                          ("big.png", PNG + b"\x00" * brand.LOGO_LIMIT)):
        path = tmp_path / "bad" / name
        if content is not None:
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(content)
        with pytest.raises(BrandSettingError, match="^KGE_LOGO must name an svg, png, jpg, jpeg or ico image"):
            configured_logo(str(path))
    folder = tmp_path / "folder.svg"
    folder.mkdir()
    with pytest.raises(BrandSettingError, match="KGE_LOGO"):
        configured_logo(str(folder))


@pytest.mark.parametrize("url, address", [(None, None), ("", None), ("http://127.0.0.1:8090/", "http://127.0.0.1:8090"),
                                          ("https://portal.example.com/discover", "https://portal.example.com/discover")])
def test_portal_url_is_an_http_address_the_page_may_link_to(url, address):
    assert portal_address(url) == address


@pytest.mark.parametrize("url", ["javascript:alert(1)", "portal.example.com", "ftp://portal", "https://", "https://p/?a=1",
                                 "https://p/#x"])
def test_any_other_portal_url_names_the_setting(url):
    with pytest.raises(BrandSettingError, match="^PORTAL_URL must be"):
        portal_address(url)


def test_the_app_refuses_to_start_on_a_bad_setting():
    text = (brand.__file__.rsplit("/kge/", 1)[0] + "/kge/app.py")
    source = open(text).read()
    assert "BRAND = Brand.from_settings()" in source
    assert "except BrandSettingError as exc:\n    raise SystemExit(str(exc)) from None" in source
    assert "BRAND.start()" in source.split("async def lifespan", 1)[1].split("yield", 1)[0]


def test_settings_name_both(monkeypatch, tmp_path):
    logo = tmp_path / "logo.png"
    logo.write_bytes(PNG)
    monkeypatch.setenv("KGE_LOGO", str(logo))
    monkeypatch.setenv("PORTAL_URL", "http://127.0.0.1:1/")
    made = Brand.from_settings()
    assert made.logo == Logo(PNG, "image/png") and made.shell() == {"logo": True, "portal_url": "http://127.0.0.1:1"}
    assert made.start() is None, "a logo of its own: the portal is not asked"


class Portal(BaseHTTPRequestHandler):
    reply: tuple[int, dict, bytes] = (200, {}, b"")
    asked = 0

    def do_GET(self):
        Portal.asked += 1
        status, headers, body = Portal.reply
        assert self.path == "/brand/logo"
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return


@pytest.fixture
def portal():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Portal)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    Portal.asked = 0
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_the_portal_s_logo_is_taken_only_when_it_was_configured_with_one(portal):
    Portal.reply = (200, {"X-Brand-Logo": "configured", "Content-Type": "image/png"}, PNG)
    assert portal_logo(portal) == (True, Logo(PNG, "image/png"))
    Portal.reply = (200, {"X-Brand-Logo": "built-in", "Content-Type": "image/svg+xml"}, SVG)
    assert portal_logo(portal) == (True, None), "the portal's built-in mark is not the client's logo"
    Portal.reply = (200, {"X-Brand-Logo": "configured"}, b"<svg><script/></svg>")
    assert portal_logo(portal) == (True, None), "the same checks as KGE_LOGO's"
    Portal.reply = (404, {}, b"")
    assert portal_logo(portal) == (True, None)


def test_a_portal_that_does_not_answer_is_asked_again_while_it_starts_then_no_more(portal, monkeypatch):
    assert portal_logo("http://127.0.0.1:9", timeout=0.5) == (False, None)
    calls = []
    answers = iter([(False, None), (False, None), (True, Logo(PNG, "image/png"))])
    monkeypatch.setattr(brand, "portal_logo", lambda url: calls.append(url) or next(answers))
    waiting = Brand(None, "http://portal.invalid")
    assert waiting.shell() == {"logo": False, "portal_url": "http://portal.invalid"}
    waiting.start(attempts=5, pause=0.01).join(5)
    assert len(calls) == 3 and waiting.logo == Logo(PNG, "image/png") and waiting.shell()["logo"] is True
    calls.clear()
    monkeypatch.setattr(brand, "portal_logo", lambda url: calls.append(url) or (False, None))
    gone = Brand(None, "http://portal.invalid")
    gone.start(attempts=3, pause=0.01).join(5)
    assert len(calls) == 3 and gone.logo is None
    stopped = Brand(None, "http://portal.invalid")
    stopped.stop()
    stopped.start(attempts=50, pause=10).join(5)
    assert len(calls) == 4, "a stopped process asks no more"
    assert Brand(None, None).start() is None, "no portal, nothing to ask"


def test_the_logo_route_serves_the_logo_with_its_type_or_is_404(monkeypatch):
    import kge.app as appmod
    app = FastAPI()
    app.include_router(tools_routes.router)
    client = TestClient(app)
    monkeypatch.setattr(appmod, "BRAND", Brand(None, None))
    for path in ("/brand/logo", "/brand/logo.svg"):
        missing = client.get(path)
        assert missing.status_code == 404 and "KGE_LOGO" in missing.json()["detail"]
    monkeypatch.setattr(appmod, "BRAND", Brand(Logo(PNG, "image/png"), "https://portal.example.com"))
    for path in ("/brand/logo", "/brand/logo.svg"):
        served = client.get(path)
        assert served.status_code == 200 and served.content == PNG
        assert served.headers["content-type"] == "image/png" and served.headers["x-content-type-options"] == "nosniff"
        assert served.headers["cache-control"] == "public, max-age=300" and served.headers["x-brand-logo"] == "configured"
        assert served.headers["content-security-policy"] == "default-src 'none'; style-src 'unsafe-inline'; sandbox"


@pytest.mark.parametrize("probe", PROBES, ids=[p["name"] for p in PROBES])
def test_the_logo_probes_the_portal_s_suite_also_runs_are_decided_alike(probe, tmp_path):
    """One probe list, portal/server/test/fixtures/logo-probes.json, for both checks, so they stay equal."""
    content = probe["svg"].encode()
    assert (logo_type(content) == "image/svg+xml") is probe["accepted"]
    path = tmp_path / "probe.svg"
    path.write_bytes(content)
    if probe["accepted"]:
        assert configured_logo(str(path)).media_type == "image/svg+xml"
    else:
        with pytest.raises(BrandSettingError, match="^KGE_LOGO must name"):
            configured_logo(str(path))
