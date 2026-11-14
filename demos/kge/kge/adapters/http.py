"""HTTP transport shared by adapters and API clients, restricted to web URLs."""
from __future__ import annotations

import urllib.error
import urllib.request
from urllib.parse import urlsplit


def _web_url(url: str) -> None:
    if urlsplit(url).scheme.lower() not in {"http", "https"}:
        raise urllib.error.URLError("only http and https URLs are allowed")


class _WebRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _web_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_http(request: str | urllib.request.Request, *, timeout: float):
    """Open only HTTP and HTTPS, including redirects, with urllib's proxy and TLS handling."""
    _web_url(request.full_url if isinstance(request, urllib.request.Request) else request)
    return urllib.request.build_opener(_WebRedirects()).open(request, timeout=timeout)
