"""The shared web transport refuses local and non-web resources before opening them."""
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from kge.adapters import http


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/data", "data:text/plain,private", "/local/file", "//example.com/file"])
@pytest.mark.parametrize("as_request", [False, True])
def test_non_http_scheme_is_refused_before_transport(monkeypatch, url, as_request):
    def unexpected(*handlers):
        pytest.fail("a refused URL reached the transport")

    monkeypatch.setattr(urllib.request, "build_opener", unexpected)
    request = urllib.request.Request(url) if as_request and ":" in url else url
    with pytest.raises(urllib.error.URLError, match="only http and https"):
        http.open_http(request, timeout=7)


@pytest.mark.parametrize("scheme", ["http", "https", "HTTPS"])
@pytest.mark.parametrize("as_request", [False, True])
def test_web_transport_preserves_request_and_timeout(monkeypatch, scheme, as_request):
    url = f"{scheme}://example.com/api"
    request = urllib.request.Request(url, data=b'{}', headers={"Content-Type": "application/json"}, method="POST") if as_request else url
    calls, response = [], object()

    def opened(value, *, timeout):
        calls.append((value, timeout))
        return response

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: SimpleNamespace(open=opened))
    assert http.open_http(request, timeout=7) is response
    assert calls == [(request, 7)]


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/data", "data:text/plain,private"])
def test_redirect_to_non_web_scheme_is_refused_without_network(url):
    class Redirect(urllib.request.HTTPHandler):
        def http_open(self, request):
            return self.parent.error("http", request, SimpleNamespace(close=lambda: None), 302, "Found", {"location": url})

    with pytest.MonkeyPatch.context() as mp:
        build = urllib.request.build_opener
        mp.setattr(urllib.request, "build_opener", lambda *handlers: build(Redirect(), *handlers))
        with pytest.raises(urllib.error.URLError):
            http.open_http("http://example.com/start", timeout=7)
