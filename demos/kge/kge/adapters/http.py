"""HTTP transport shared by adapters and API clients, restricted to web URLs. The one place KGE adds trace context to an
outgoing call (kge/pipeline/trace.py trace_headers): only to a destination TRACE_PROPAGATE_TO lists, and a redirect to
any other drops it. urllib's calls go through open_http; httpx's through http_client and async_http_client, and the
Anthropic SDK's (httpx2) through sdk_http_client, whose transports strip and add the same headers on every request,
redirects included, and also send KGE's own User-Agent and no X-Stainless-* header, the SDK's description of its
version, the runtime and the machine."""
from __future__ import annotations

import urllib.error
import urllib.request
from urllib.parse import urlsplit

from kge.pipeline.telemetry import TRACE_HEADERS
from kge.pipeline.trace import trace_headers


def _web_url(url: str) -> None:
    if urlsplit(url).scheme.lower() not in {"http", "https"}:
        raise urllib.error.URLError("only http and https URLs are allowed")


class _WebRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _web_url(newurl)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            _traced(redirected)
        return redirected


def _traced(request: urllib.request.Request) -> urllib.request.Request:
    """request with the trace context its destination may carry, and no other trace or correlation header
    (TRACE_HEADERS), whoever set it."""
    for store in (request.headers, request.unredirected_hdrs):
        for name in [name for name in store if name.lower() in TRACE_HEADERS]:
            del store[name]
    for name, value in trace_headers(request.full_url).items():
        request.add_header(name, value)
    return request


def open_http(request: str | urllib.request.Request, *, timeout: float):
    """Open only HTTP and HTTPS, including redirects, with urllib's proxy and TLS handling."""
    _web_url(request.full_url if isinstance(request, urllib.request.Request) else request)
    if isinstance(request, urllib.request.Request):
        request = _traced(request)
    elif headers := trace_headers(request):
        request = urllib.request.Request(request, headers=headers)
    return urllib.request.build_opener(_WebRedirects()).open(request, timeout=timeout)


USER_AGENT = "kge"


def _httpx_traced(request) -> None:
    """request (an httpx or httpx2 Request) with the trace context its destination may carry, no other trace or
    correlation header and no X-Stainless-* header, whoever set it, and KGE's User-Agent: _traced's rule for httpx."""
    for name in [name for name in request.headers
                 if name.lower() in TRACE_HEADERS or name.lower().startswith("x-stainless-") or name.lower() == "user-agent"]:
        del request.headers[name]
    request.headers["User-Agent"] = USER_AGENT
    request.headers.update(trace_headers(str(request.url)))


def http_client(**options):
    """An httpx.Client (options as httpx takes them) whose every request goes through _httpx_traced."""
    import httpx

    class Traced(httpx.BaseTransport):
        def __init__(self) -> None:
            self.inner = httpx.HTTPTransport()

        def handle_request(self, request):
            _httpx_traced(request)
            return self.inner.handle_request(request)

        def close(self) -> None:
            self.inner.close()
    return httpx.Client(transport=Traced(), **options)


def async_http_client(*, app=None, tasks=None, **options):
    """An httpx.AsyncClient (options as httpx takes them) whose every request goes through _httpx_traced: to the network,
    or, given an ASGI app, to that app in this process, each response's body as the app sends it, the app's calls run
    in tasks, an anyio task group the caller holds open while it reads (LiveASGITransport)."""
    import httpx

    class Traced(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.inner = LiveASGITransport(app, tasks) if app is not None else httpx.AsyncHTTPTransport()

        async def handle_async_request(self, request):
            _httpx_traced(request)
            return await self.inner.handle_async_request(request)

        async def aclose(self) -> None:
            await self.inner.aclose()
    return httpx.AsyncClient(transport=Traced(), **options)


class LiveASGITransport:
    """Requests to an ASGI app in this process whose response body reaches the client as the app sends it: httpx's own
    ASGITransport holds the body until the app ends, so an event stream's events would all arrive with the answer. Each
    request's app call runs in tasks, a task group the caller holds open while it reads; the app sees the client go
    (http.disconnect) once the body is closed or the app has ended. An httpx async transport; async_http_client's
    wraps it, so its requests are traced as every other."""

    def __init__(self, app, tasks):
        self.app = app
        self.tasks = tasks

    async def handle_async_request(self, request):
        import anyio
        import httpx
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": request.method,
                 "headers": [(k.lower(), v) for k, v in request.headers.raw], "scheme": request.url.scheme,
                 "path": request.url.path, "raw_path": request.url.raw_path.split(b"?")[0],
                 "query_string": request.url.query, "server": (request.url.host, request.url.port),
                 "client": ("127.0.0.1", 123), "root_path": ""}
        chunks = request.stream.__aiter__()
        body_in, body_out = anyio.create_memory_object_stream[bytes](max_buffer_size=float("inf"))
        started, gone = anyio.Event(), anyio.Event()
        head: dict = {}
        sent = False

        async def receive() -> dict:
            nonlocal sent
            if sent:
                await gone.wait()
                return {"type": "http.disconnect"}
            try:
                chunk = await chunks.__anext__()
            except StopAsyncIteration:
                sent = True
                return {"type": "http.request", "body": b"", "more_body": False}
            return {"type": "http.request", "body": chunk, "more_body": True}

        async def send(message: dict) -> None:
            if message["type"] == "http.response.start":
                head.update(status=message["status"], headers=message.get("headers", []))
                started.set()
            elif message["type"] == "http.response.body":
                if message.get("body"):
                    try:
                        await body_in.send(message["body"])
                    except (anyio.BrokenResourceError, anyio.ClosedResourceError):
                        pass
                if not message.get("more_body", False):
                    body_in.close()

        async def run() -> None:
            try:
                await self.app(scope, receive, send)
            except Exception as exc:
                head.setdefault("error", exc)
            finally:
                body_in.close()
                gone.set()
                started.set()
        self.tasks.start_soon(run)
        await started.wait()
        if "status" not in head:
            raise head.get("error") or httpx.RemoteProtocolError("the app sent no response", request=request)
        return httpx.Response(head["status"], headers=head["headers"], stream=live_body(body_out, gone))

    async def aclose(self) -> None:
        return None


def live_body(chunks, gone):
    """A response body, each chunk as the app sends it (chunks, a memory stream); closing it tells the app the client
    has gone (gone, an anyio Event): an httpx.AsyncByteStream."""
    import httpx

    class Live(httpx.AsyncByteStream):
        async def __aiter__(self):
            async for chunk in chunks:
                yield chunk

        async def aclose(self) -> None:
            gone.set()
            chunks.close()
    return Live()


def sdk_http_client(**options):
    """The HTTP client an SDK built on httpx2 (the Anthropic SDK) is given (an httpx2.Client, options as it takes
    them), whose every request goes through _httpx_traced."""
    import httpx2

    class Traced(httpx2.BaseTransport):
        def __init__(self) -> None:
            self.inner = httpx2.HTTPTransport()

        def handle_request(self, request):
            _httpx_traced(request)
            return self.inner.handle_request(request)

        def close(self) -> None:
            self.inner.close()
    return httpx2.Client(transport=Traced(), **options)
