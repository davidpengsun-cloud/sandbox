"""The client's logo and the portal's address, for the bar the page and the discovery portal share.

KGE_LOGO names a logo file, checked once as the process starts, as the portal checks PORTAL_LOGO: an SVG, PNG, JPEG or
ICO image of 256 KB or less, its type read from its content, and an SVG a plain drawing (safe_svg). One that fails stops the process, naming the setting. Unset, and with PORTAL_URL naming the portal, the process asks
the portal for its logo as it starts (`/brand/logo`), takes it only when the portal says it was configured with one
(`X-Brand-Logo: configured`) and it passes the same checks, and keeps it in memory; a portal that does not answer is
asked again a few times while it starts, then no more. Neither, no logo: the page shows the client's name instead.
"""
from __future__ import annotations

import re
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from kge.pipeline.settings import settings

LOGO_LIMIT = 256 * 1024
LOGO_SUFFIXES = (".svg", ".png", ".jpg", ".jpeg", ".ico")
LOGO_REFUSED = ("KGE_LOGO must name an svg, png, jpg, jpeg or ico image of 256 KB or less; "
                "an SVG must be a plain drawing, with no doctype, script, event handler, animation, style sheet, embedded image, character reference or external link.")
PORTAL_URL_REFUSED = "PORTAL_URL must be the discovery portal's http or https address, such as https://portal.example.com."
SIGNATURES = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"\x00\x00\x01\x00", "image/x-icon"))
SVG_ELEMENTS = frozenset((
    "svg g defs title desc metadata path rect circle ellipse line polyline polygon text tspan use symbol linearGradient "
    "radialGradient stop clipPath mask pattern marker filter feBlend feColorMatrix feComponentTransfer feComposite "
    "feDropShadow feFlood feFuncA feFuncB feFuncG feFuncR feGaussianBlur feMerge feMergeNode feMorphology feOffset").split())
SVG_NAMESPACES = {"xmlns": "http://www.w3.org/2000/svg", "xmlns:xlink": "http://www.w3.org/1999/xlink"}
STYLE_FUNCTIONS = frozenset(("rgb", "rgba", "hsl", "hsla", "url"))
XML_DECLARATION = re.compile(r"<\?xml[ \t\n\r][^<>?]*\?>")
SVG_TOKEN = re.compile(r"<!--([^<>-]*(?:-[^<>-]+)*)-->|</([A-Za-z][A-Za-z0-9-]*)[ \t\n\r]*>"
                       r"|<([A-Za-z][A-Za-z0-9-]*)((?:[ \t\n\r]+[A-Za-z][A-Za-z0-9:._-]*[ \t\n\r]*=[ \t\n\r]*"
                       r"(?:\"[^\"<]*\"|'[^'<]*'))*)[ \t\n\r]*(/?)>|([^<]+)")
SVG_ATTRIBUTE = re.compile(r"[ \t\n\r]+([A-Za-z][A-Za-z0-9:._-]*)[ \t\n\r]*=[ \t\n\r]*(?:\"([^\"<]*)\"|'([^'<]*)')")
SVG_REFUSED = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u2028\u2029\ufeff\ufffe\uffff\\]|&#|&(?!(?:amp|lt|gt|quot|apos);)"
                         r"|<!(?!--)|<\?|@import|data:|javascript:|url[ \t\n\r]*\((?![ \t\n\r'\"]*#)", re.I)
LOCAL_REFERENCE = re.compile(r"#[A-Za-z_][A-Za-z0-9_.-]*")
LOGO_POLICY = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
PORTAL_ATTEMPTS = 24
PORTAL_PAUSE_SECONDS = 5.0
PORTAL_TIMEOUT_SECONDS = 2.0


class BrandSettingError(ValueError):
    """KGE_LOGO or PORTAL_URL names something the process cannot use; the message names the setting."""


@dataclass(frozen=True)
class Logo:
    content: bytes
    media_type: str


def logo_type(content: bytes) -> str | None:
    """The image type a logo's bytes hold, or None when they are no logo this process serves."""
    if not content or len(content) > LOGO_LIMIT:
        return None
    for signature, media_type in SIGNATURES:
        if content.startswith(signature):
            return media_type
    return "image/svg+xml" if safe_svg(content) else None


def safe_svg(content: bytes) -> bool:
    """Whether the bytes are a simple logo: UTF-8 text, an optional XML declaration and comments, then one svg root
    holding only the drawing elements a logo needs (SVG_ELEMENTS), every attribute quoted and unprefixed but for xmlns,
    xmlns:xlink and xlink:href, no doctype, entity, character reference, processing instruction, backslash, event
    handler, animation, script, style sheet or data, and no reference or url( to anything outside the image itself.
    portal/server/src/brand.js applies the same rule; both run portal/server/test/fixtures/logo-probes.json."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    text = text[1:] if text.startswith("\ufeff") else text
    declaration = XML_DECLARATION.match(text)
    text = text[declaration.end():] if declaration else text
    if SVG_REFUSED.search(text):
        return False
    open_elements: list[str] = []
    roots = 0
    position = 0
    while position < len(text):
        token = SVG_TOKEN.match(text, position)
        if token is None:
            return False
        position = token.end()
        _, closing, opening, attributes, empty, words = token.groups()
        if words is not None and not open_elements and words.strip(" \t\n\r"):
            return False
        if closing is not None:
            if not open_elements or open_elements.pop() != closing:
                return False
        if opening is not None:
            roots += not open_elements
            if opening not in SVG_ELEMENTS or (not open_elements and (opening != "svg" or roots > 1)):
                return False
            if not safe_attributes(SVG_ATTRIBUTE.findall(attributes)):
                return False
            if not empty:
                open_elements.append(opening)
    return roots == 1 and not open_elements


def safe_attributes(found: list[tuple[str, str, str]]) -> bool:
    """Whether a tag's attributes are a logo's: unique, unprefixed but for the SVG and XLink namespaces and xlink:href,
    no event handler or animation target, references to the image's own ids, and styles calling colour functions."""
    names = [name for name, _, _ in found]
    if len(names) != len(set(names)):
        return False
    for name, double, single in found:
        value = double or single
        if ":" in name and name not in ("xmlns:xlink", "xlink:href"):
            return False
        if name in SVG_NAMESPACES and value != SVG_NAMESPACES[name]:
            return False
        if name.lower().startswith("on") or name.lower() == "attributename":
            return False
        if name in ("href", "xlink:href") and not LOCAL_REFERENCE.fullmatch(value):
            return False
        if name == "style" and any(called.lower() not in STYLE_FUNCTIONS for called in re.findall(r"([A-Za-z-]*)[ \t\n\r]*\(", value)):
            return False
    return True


def configured_logo(path: str | None) -> Logo | None:
    """The logo KGE_LOGO names, checked; None when it is unset. A file that fails a check is a BrandSettingError."""
    if not (path or "").strip():
        return None
    file = Path(path.strip())
    if file.suffix.lower() not in LOGO_SUFFIXES or not file.is_file() or file.stat().st_size > LOGO_LIMIT:
        raise BrandSettingError(LOGO_REFUSED)
    content = file.read_bytes()
    media_type = logo_type(content)
    if media_type is None or (media_type == "image/svg+xml") != (file.suffix.lower() == ".svg"):
        raise BrandSettingError(LOGO_REFUSED)
    return Logo(content, media_type)


def portal_address(url: str | None) -> str | None:
    """The portal's address PORTAL_URL gives, without a trailing slash; None when it is unset. Anything but an http or
    https address with a host is a BrandSettingError, since the page links to it."""
    if not (url or "").strip():
        return None
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.query or parts.fragment:
        raise BrandSettingError(PORTAL_URL_REFUSED)
    return url.strip().rstrip("/")


def portal_logo(portal: str, timeout: float = PORTAL_TIMEOUT_SECONDS) -> tuple[bool, Logo | None]:
    """Ask the portal for its logo once: whether it answered, and the logo when it holds a configured one that passes
    the checks."""
    request = urllib.request.Request(portal + "/brand/logo", headers={"Accept": "image/*"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.headers.get("X-Brand-Logo", "").strip().lower() != "configured":
                return True, None
            content = response.read(LOGO_LIMIT + 1)
    except urllib.error.HTTPError:
        return True, None
    except (OSError, ValueError):
        return False, None
    media_type = logo_type(content)
    return True, Logo(content, media_type) if media_type else None


class Brand:
    """The bar's brand for this process: the logo it serves, if any, and the portal's address, if one is set."""

    def __init__(self, logo: Logo | None, portal: str | None):
        self.logo = logo
        self.portal = portal
        self._asked = threading.Event()

    @classmethod
    def from_settings(cls) -> "Brand":
        current = settings()
        return cls(configured_logo(current.KGE_LOGO), portal_address(current.PORTAL_URL))

    def start(self, attempts: int = PORTAL_ATTEMPTS, pause: float = PORTAL_PAUSE_SECONDS) -> threading.Thread | None:
        """With no logo of its own and a portal to ask, ask it in the background until it answers or the attempts run
        out; the process starts and serves meanwhile, its page showing the client's name."""
        if self.logo is not None or self.portal is None:
            return None

        def ask() -> None:
            for _ in range(attempts):
                answered, logo = portal_logo(self.portal)
                if answered:
                    self.logo = logo
                    return
                if self._asked.wait(pause):
                    return

        thread = threading.Thread(target=ask, name="portal-logo", daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self._asked.set()

    def shell(self) -> dict:
        """What the page's bar needs: whether a logo is served at /brand/logo, and the portal's address."""
        return {"logo": self.logo is not None, "portal_url": self.portal}
