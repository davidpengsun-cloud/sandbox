"""The two standalone pages share a palette; KGE ships its fonts without an asset server."""

import base64
import re
from pathlib import Path
from urllib.parse import urlsplit

from tests.unit.test_page import Markup, _function, _style, _token_blocks

DEMO = Path(__file__).resolve().parents[2]
PAGE = DEMO / "ui/index.html"
PORTAL_TOKENS = DEMO / "portal/ui/src/assets/styles/v3-tokens.css"


def properties(css):
    """Parse custom declarations, ignoring comments and insignificant value whitespace.

    These token values are colours, dimensions, shadows and quoted font stacks;
    preserve quoted strings (including spaces within a font name) as single tokens.
    """
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    declarations = re.findall(r"(--[\w-]+)\s*:\s*([^;{}]+)[;}]", css)
    assert len(declarations) == len({name for name, _ in declarations}), "duplicate token declaration"
    return {name: tuple(re.findall(r"'[^']*'|\"[^\"]*\"|[\w.#%-]+|[^\s]", value))
            for name, value in declarations}


def test_kge_embedded_tokens_match_the_portal_by_name_and_value_in_light_and_dark():
    """Three blocks in each copy: the light tokens, the dark ones the system setting applies and the dark ones a viewer's
    choice applies. The page's three equal the portal's, and in each file both dark blocks are the same set."""
    portal = [properties(block) for block in _token_blocks(PORTAL_TOKENS.read_text())[:3]]
    css = _style()
    root, media, chosen, rest = _token_blocks(css)
    page = [properties(block) for block in (root, media, chosen)]
    assert all(portal) and page == portal
    assert portal[1] == portal[2] and page[1] == page[2], "the two dark blocks differ"
    assert set(portal[1]) < set(portal[0]), "a dark token has no light value"
    aliases = properties(rest)
    assert not [name for name in aliases if name.startswith("--v3-")], "a --v3- token declared outside the token copy"
    assert "assets/styles/v3-tokens.css" in (DEMO / "portal/ui/src/index.js").read_text()
    for alias, token in (("sans", "body"), ("mono", "mono"), ("bg", "paper"), ("panel", "surface"),
                         ("radius", "r-md"), ("radius-sm", "r-sm"), ("shadow", "shadow-2")):
        assert aliases[f"--{alias}"] == ("var", "(", f"--v3-{token}", ")")
    for alias in ("accent", "ok", "warn", "bad", "info"):
        assert aliases[f"--{alias}"] == ("var", "(", f"--v3-{alias}", ")"), f"--{alias} reads its role, so dark mode follows"


def test_the_theme_a_viewer_chose_is_applied_before_the_styles():
    html = PAGE.read_text()
    head = html.split("</head>", 1)[0]
    assert head.index("<script>") < head.index("<style>"), "the page would flash the other theme"
    early = head.split("<script>", 1)[1].split("</script>", 1)[0]
    assert 'localStorage.getItem("v3-theme")' in early and "try {" in early and "dataset.theme" in early
    assert "color-scheme:light" not in _style().replace(" ", "").split(":root{", 2)[2].split("}", 1)[0], \
        "the alias block would pin the light scheme in dark mode"


def test_page_styles_scripts_and_fonts_need_no_external_resource():
    html = PAGE.read_text()
    page = Markup()
    page.feed(html)
    for tag, attrs, _ in page.elements:
        for key in ("src", "href", "srcset", "poster", "data"):
            value = attrs.get(key, "").strip()
            if not value:
                continue
            parsed = urlsplit(value)
            assert not parsed.netloc and parsed.scheme in ("", "data"), (tag, key, value)
        assert tag != "base", "a base URL could send relative API calls elsewhere"
        if tag in ("script", "link"):
            assert not attrs.get("src") and not attrs.get("href"), "the page is served without static routes"
    styles = "\n".join(re.findall(r"<style\b[^>]*>(.*?)</style>", html, flags=re.S))
    assert not re.search(r"@import\b", styles, re.I)
    urls = re.findall(r"url\(\s*['\"]?([^)'\"]+)", styles, re.I)
    assert urls and all(url.startswith("data:font/woff2;base64,") for url in urls)
    assert not re.search(r"https?://|fonts\.googleapis|fonts\.gstatic", page.script)


def test_bundled_font_faces_have_valid_woff2_payloads_weights_and_notices():
    faces = re.findall(r"@font-face\s*\{([^}]+)\}", PAGE.read_text())
    weights = {}
    ranges = set()
    for face in faces:
        family = re.search(r"font-family:\s*'([^']+)'", face)[1]
        weight = int(re.search(r"font-weight:\s*(\d+)", face)[1])
        coverage = re.search(r"unicode-range:\s*([^;]+)", face)[1]
        key = (family, weight, coverage)
        assert key not in ranges
        ranges.add(key)
        weights.setdefault(family, set()).add(weight)
        payload = re.search(r"data:font/woff2;base64,([A-Za-z0-9+/=]+)", face)[1]
        data = base64.b64decode(payload, validate=True)
        assert data[:4] == b"wOF2" and int.from_bytes(data[8:12], "big") == len(data)
        assert "font-display: swap" in face
    assert weights == {"Inter": {400, 500, 600, 700}, "Geist Mono": {400, 500, 600}}
    assert len(faces) == len({(family, weight) for family, weight, _ in ranges}), "one face per family and weight"
    assert all(coverage.startswith("U+0000-00FF,") for _, _, coverage in ranges), \
        "inlined faces cannot load on demand, so only the latin subset is embedded"
    notices = (DEMO / "ui/FONT_NOTICES.md").read_text()
    for name in ("inter", "geist-mono"):
        assert f"@fontsource/{name} 5.3.0" in notices
        assert f"@fontsource/{name}@5.3.0: OFL-1.1" in (DEMO / "portal/THIRD_PARTY_NOTICES.md").read_text()
    assert "Copyright 2016 The Inter Project Authors" in notices
    assert "Copyright 2024 The Geist Project Authors" in notices
    assert notices.count("SIL OPEN FONT LICENSE Version 1.1") == 2
    assert "COPY ui ui" in (DEMO / "Dockerfile").read_text()


def test_brand_uses_the_existing_profile_title_and_seven_panels_remain():
    page = Markup()
    page.feed(PAGE.read_text())
    _, profile = _function(page.script, "loadProfile")
    assert profile.count('api("/profile")') == 1 and profile.count("await api(") == 1
    assert "#title" not in profile and "document.title" not in profile
    assert 'showBrand(p.title || "Governance graph", p.shell || {})' in profile
    assert page.by_id("title")[1][0] == "h1"
    panels = {attrs["id"] for tag, attrs, _ in page.elements
              if tag == "section" and "panel" in attrs.get("class", "").split()}
    assert panels == {"assistant", "detail", "steward", "onboard", "control", "sql-check", "mcp-config", "no-view"}


def test_the_portal_applies_the_remembered_theme_before_any_stylesheet_or_script():
    """The portal's index.html: its inline theme script is the first thing in <head>, before every stylesheet, link and
    module script, so a reload draws the chosen mode from the first paint; as built too, where the build is there."""
    for html_file in (DEMO / "portal/ui/index.html", DEMO / "portal/ui/build/index.html"):
        if not html_file.exists():
            continue
        head = html_file.read_text().split("</head>", 1)[0]
        first = re.search(r"<script>(.*?)</script>", head, re.S)
        assert first and 'localStorage.getItem("v3-theme")' in first.group(1) and "try" in first.group(1), html_file
        later = [m.start() for m in re.finditer(r"<link\b|<style\b|<script\b[^>]*\bsrc=", head)]
        assert later and first.start() < min(later), f"{html_file}: a stylesheet or script comes before the theme"
