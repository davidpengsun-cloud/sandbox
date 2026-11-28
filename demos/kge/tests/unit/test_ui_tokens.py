"""The developer page keeps bundled fonts; Context inherits portal tokens and theme."""

import base64
import re
from pathlib import Path


DEMO = Path(__file__).resolve().parents[2]
PAGE = DEMO / "ui/fonts.html"


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
