"""The retrieval API and the assistant's back end.

    uvicorn kge.app:app --app-dir . --host 127.0.0.1 --port 8000

Four endpoints do the work the PoC brief names, two answer a question end to
end (/answer, and /ask, which adds the documents behind the figures), two
serve the steward, one runs the control. /agent runs the reference agent of
the A/B test (kge/services/ask/engine/agent.py) on one arm, graph off or graph on; /context gives
the context package that agent's prompt carries on that arm, built by the
context workflow (kge/services/ask/engine/context.py), and /mcp serves it, /retrieve and /map/{term}
as the tools of an MCP server (get_context, retrieve, map_term), so another
agent can get its context through either, as /agent's own gets it through
/context (in this process, serving);
/ab lists the A/B harness's runs
(make ab) and serves each one's report. /profile and
/golden name, for the page at /, whom the process serves (the pack's title
among it) and the profile's golden questions, its sample questions: the page
names no client of its own, and calls nothing else. Every answer carries the
queries it ran.

Both answering endpoints take an arm, one of the presets GET /presets lists,
and optionally a stages override of the run config (kge/services/ask/engine/config.py). An override
that cannot run on that endpoint, or asks for a model with none configured, is
a 400 that says why; the answer names the config it ran on, and its run_trace
(kge/services/ask/engine/runtrace.py) what each stage took, read, asked a model and bound, by id.

The process serves one profile (kge/pipeline/profile.py), resolved once at import:
PROFILE, else the registry's default (deploy/profiles.yaml). Its client's graph,
warehouses and runtime overlay are the only ones it opens: the graph is the one
of the artifact the client's current pointer names when the engine starts, kept
until the process restarts, whatever make release swaps meanwhile. Its run state
(Profile.run_dir: the tokens, this process's pid, the A/B runs, the pipeline's
stats and the eval) is the only one it reads and writes. No request names a
profile: a stages override that does is a 400, and the run config of every
answer carries the process's, which its run trace records.

Identity comes from the server: a bearer token or a session cookie (see
kge/services/identity/service.py), never a principal named in the request body. The one
exception is demo mode, off unless DEMO_MODE=1, which lets a caller on this
machine sign in by name; the server still opens that session through the
principal's own token, and refuses the call from any other address. Pilot
sign-in, off unless SIGNIN=passwords, lets a person sign in with a username
and the password make passwords made for it, from anywhere, through POST
/session: the session is opened through that principal's own token, as demo
mode opens one, sign-in is limited per username and per client address
(Throttle): the address a trusted caller forwards (TRUSTED_CALLER, the
trusted frontend on the pilot, whose Google ID token KGE verifies itself),
else the caller's own; and the session is a signed cookie that outlives the process
(SignedSessions). Unset, sessions are held in the process, as before. The
cookie is HttpOnly and SameSite either way, and Secure over HTTPS. Each endpoint
names the process it needs: querying for questions and retrieval,
certification for the steward queue. Tagging has no endpoint; only the
pipeline tags. /mcp takes a bearer token alone: every tool call runs as the
principal whose token the MCP request carries, and one with no token, or a bad
one, is refused as the API refuses it. A body with a field its endpoint does not
take, a principal among them, is an error, not a hint. Each profile's session
cookie has a name of its own, so another profile's page on this host keeps its
own session. A steward decision the graph took but the overlay's journal could
not record is a 500, since the next load would lose it.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
from contextlib import asynccontextmanager, contextmanager, ExitStack
from contextvars import ContextVar
from html import escape
from pathlib import Path

import anyio
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html, get_swagger_ui_oauth2_redirect_html
from fastapi.responses import JSONResponse

from kge.services.ask.engine.agent import Agent
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.context import as_json as as_json
from kge.services.http import principal as principal
from kge.services.http import (DEADLINE_ERRORS, FAILURE_KIND, REQUEST_SPAN, RequestLimitsMiddleware, RequestSpan, in_process_hop,
                               request_validation_failed, require_released)
from kge.services.ask.engine.core import Engine
from kge.services.identity.service import Identities, SignedSessions, Throttle, session_cookie
from kge.adapters.sessionstate import identity_refresher, for_profile as identity_state
from kge.services.ask import mcp, routes as ask
from kge.services.access import routes as access, mcp as access_mcp
from kge.services.identity import routes as identity
from kge.services.identity import mcp as identity_mcp
from kge.services.catalogue import routes as catalogue
from kge.services.catalogue import mcp as catalogue_mcp
from kge.services.data import routes as data
from kge.services.data import mcp as data_mcp
from kge.services.assist import routes as assist, mcp as assist_mcp
from kge.services.search import routes as search
from kge.services.search import mcp as search_mcp
from kge.services.graph import routes as graph, mcp as graph_mcp
from kge.services.onboard import routes as onboard
from kge.services.onboard import mcp as onboard_mcp
from kge.services.quality import routes as quality
from kge.services.steward import routes as steward
from kge.services.tools import routes as tools
from kge.pipeline.envfile import load_env
from kge.pipeline.access_table import Access
from kge.adapters.signin import for_profile as password_source
from kge.adapters.brand import Brand, BrandSettingError
from kge.adapters.caller import TrustedCallerSettingError, trusted_caller
from kge.adapters.model import ExternalCallError, ModelSettingError, check_settings
from kge.adapters.tracing import FLUSH_SECONDS, TracingSettingError, flush
from kge.adapters.tracing import check_settings as check_tracing_settings
from kge.pipeline.telemetry import FASTAPI_TELEMETRY_OFF
from kge.pipeline.trace import as_current, failed, new_span
from kge.pipeline.profile import ProfileError, resolve
from kge.pipeline.settings import check_service_auth_settings, product_name, settings

load_env()
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
PROFILE = resolve().serve()
SERVE_HASH = (settings().SERVE_HASH or "").strip()
VERSION = (settings().KGE_VERSION or "dev").strip() or "dev"
SERVE_RELEASED = False
if SERVE_HASH:
    if not re.fullmatch(r"[0-9a-f]{64}", SERVE_HASH):
        raise SystemExit(f"SERVE_HASH={SERVE_HASH}: must be a 64-character lower-case hexadecimal hash")
    PROFILE = PROFILE.at(SERVE_HASH)
    candidate_graph = PROFILE.graph_path()
    if not candidate_graph.exists() or not (candidate_graph.parent / "LOADED").is_file():
        raise SystemExit(f"SERVE_HASH={SERVE_HASH}: candidate is not loaded")
    current = PROFILE.current()
    SERVE_RELEASED = (current is not None and current.name == SERVE_HASH) or (candidate_graph.parent / "RELEASED").is_file()
COOKIE = session_cookie(PROFILE.id)
DEMO_MODE = (settings().DEMO_MODE or "").strip().lower() not in ("", "0", "false", "no", "off")
SIGNIN = (settings().SIGNIN or "").strip().lower()
if SIGNIN not in ("", "passwords"):
    raise SystemExit(f"SIGNIN={SIGNIN!r}: passwords for the pilot's sign-in, or unset for the demo's (a token, or a name in demo mode)")
try:
    check_settings()
except ModelSettingError as exc:
    raise SystemExit(str(exc)) from None
try:
    check_tracing_settings()
except TracingSettingError as exc:
    raise SystemExit(str(exc)) from None
try:
    check_service_auth_settings()
except ValueError as exc:
    raise SystemExit(str(exc)) from None
try:
    BRAND = Brand.from_settings()
except BrandSettingError as exc:
    raise SystemExit(str(exc)) from None
PASSWORDS = SIGNIN == "passwords"
THROTTLE = Throttle()
try:
    TRUSTED_CALLER = trusted_caller()
except TrustedCallerSettingError as exc:
    raise SystemExit(str(exc)) from None
LOOPBACK = {"127.0.0.1", "::1"}
RUN_ID = re.compile(r"^[0-9A-Za-z][0-9A-Za-z_-]*$")
SIDES = {"control": "graph off", "treatment": "graph on"}
DEFAULT_ARM = "graph"
_engine: Engine | None = None
UNRELEASED_MESSAGE: str | None = None
_combined: Combined | None = None
_agent: Agent | None = None
_ids: Identities | None = None
_identity_lifespan: ExitStack | None = None
_serving: ContextVar[Agent | None] = ContextVar("serving", default=None)
_request_bundle: ContextVar[tuple[Engine, Combined, Agent] | None] = ContextVar("request_bundle", default=None)
_swap_lock = threading.RLock()
_references: dict[int, int] = {}
_retired: dict[int, Engine] = {}
SIGN_IN = "sign in: POST /session with a token, or send Authorization: Bearer <token> (python3 -m kge.services.identity.cli prints the demo tokens)"


def engine() -> Engine:
    global _engine, UNRELEASED_MESSAGE
    if _serving.get() is not None:
        return _serving.get().e
    if _request_bundle.get() is not None:
        return _request_bundle.get()[0]
    if _engine is None:
        require_released()
        try:
            _engine = Engine.serving(PROFILE)
        except (ProfileError, FileNotFoundError):
            if PROFILE.model_hash() is not None:
                raise
            UNRELEASED_MESSAGE = f"nothing released for {PROFILE.client}: release first"
            logging.getLogger(__name__).warning("%s", UNRELEASED_MESSAGE)
            require_released()
    return _engine


def combined() -> Combined:
    global _combined
    if _request_bundle.get() is not None:
        return _request_bundle.get()[1]
    if _combined is None:
        _combined = Combined(engine())
    return _combined


def agent() -> Agent:
    """The reference agent, on the same engine and the configured model as /ask; on the mock too
    (MODEL_PROVIDER=mock), which /ask does not use."""
    global _agent
    if _serving.get() is not None:
        return _serving.get()
    if _request_bundle.get() is not None:
        return _request_bundle.get()[2]
    if _agent is None:
        _agent = Agent(engine())
    return _agent


def ids() -> Identities:
    global _ids
    if _serving.get() is not None:
        return _serving.get().identities
    if _ids is None:
        with _swap_lock:
            if _ids is None:
                people = Access.for_profile(PROFILE)
                state = identity_state(PROFILE)
                _ids = Identities(PROFILE.run_dir(), people.people,
                                  SignedSessions(PROFILE.run_dir(), PROFILE.id, state=state) if PASSWORDS else None,
                                  access=people, passwords=password_source(PROFILE, settings().SIGNIN_PASSWORDS) if PASSWORDS else None,
                                  state=state)
                if _identity_lifespan is not None:
                    _identity_lifespan.enter_context(identity_refresher(state))
    return _ids


@contextmanager
def refreshing_identities():
    """Own identities and refreshers created at startup or after the first release for this lifespan."""
    global _identity_lifespan, _ids
    with ExitStack() as stack:
        _identity_lifespan = stack
        try:
            if _ids is not None:
                stack.enter_context(identity_refresher(_ids.state))
            yield
        finally:
            _identity_lifespan = None
            _ids = None


def runtimes_health() -> dict[str, dict] | None:
    """The active engine's runtime health map when an engine is already open, without opening one."""
    eng = _serving.get().e if _serving.get() is not None else (_request_bundle.get()[0] if _request_bundle.get() is not None else _engine)
    if eng is None:
        return None
    return eng.w.runtimes_health()


@contextmanager
def serving(ag: Agent):
    """For a call made in this process, the routes serve ag: its engine, the agent itself and the identities of its
    profile. So the reference agent reaches POST /context through the route, the token check and the JSON the API serves
    it with, in whatever process it runs (kge/services/ask/engine/context.py, post): the API's own, make agent's or the A/B harness's, on its
    own graph."""
    token = _serving.set(ag)
    try:
        with in_process_hop():
            yield
    finally:
        _serving.reset(token)


def close() -> None:
    """Close the graph on a normal stop, so the store checkpoints it: no graph.<store>.wal is
    left behind for the next process, make graph included. The next startup opens it again."""
    global _engine, _combined, _agent
    with _swap_lock:
        held = [e for e in [_engine, *_retired.values()] if e is not None]
        _engine = _combined = _agent = None
        _retired.clear()
    for opened in dict.fromkeys(held):
        opened.close()


def switch() -> None:
    """Open current, swap it into service, and retire the old engine after its last request.

    Identities are deliberately not replaced: browser sessions survive a release.
    """
    global _engine, _combined, _agent, UNRELEASED_MESSAGE
    fresh_engine = Engine.serving(PROFILE)
    fresh_combined = Combined(fresh_engine)
    fresh_agent = Agent(fresh_engine)
    closing = None
    with _swap_lock:
        old = _engine
        _engine, _combined, _agent = fresh_engine, fresh_combined, fresh_agent
        UNRELEASED_MESSAGE = None
        if old is not None:
            key = id(old)
            if _references.get(key, 0):
                _retired[key] = old
            else:
                closing = old
    if closing is not None:
        closing.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """The app's life: its title follows the operator's display name; it writes its pid and
    address to the run state, so a locked-graph message can name this process and the way round it; and it runs /mcp's
    session manager for as long as the app serves."""
    _app.title = product_name()
    _app.openapi_schema = None
    if PASSWORDS:
        logging.getLogger(__name__).warning("session key fingerprint=%s", SignedSessions(PROFILE.run_dir(), PROFILE.id).fingerprint)
    try:
        engine()
        ids()
    except HTTPException:
        if UNRELEASED_MESSAGE is None:
            raise
    with refreshing_identities():
        BRAND.start()
        pidfile = PROFILE.run_dir() / "api.pid"
        serving = PROFILE.run_dir() / "api.json"
        pidfile.parent.mkdir(parents=True, exist_ok=True)
        pidfile.write_text(str(os.getpid()))
        serving.write_text(json.dumps({"pid": os.getpid(), "profile": PROFILE.id, "url": address()}) + "\n")
        global context_mcp, onboarding_mcp, answer_mcp, identity_capability_mcp, catalogue_capability_mcp, data_capability_mcp, search_capability_mcp, access_capability_mcp, assist_capability_mcp, graph_capability_mcp
        context_mcp = mcp.mcp_server()
        onboarding_mcp = onboard_mcp.mcp_server(mcp.tool)
        answer_mcp = mcp.answer_mcp_server()
        identity_capability_mcp = identity_mcp.mcp_server()
        catalogue_capability_mcp = catalogue_mcp.mcp_server()
        data_capability_mcp = data_mcp.mcp_server()
        assist_capability_mcp = assist_mcp.mcp_server(mcp.tool)
        search_capability_mcp = search_mcp.mcp_server(mcp.tool)
        access_capability_mcp = access_mcp.mcp_server()
        graph_capability_mcp = graph_mcp.mcp_server(mcp.tool)
        context_endpoint.server = context_mcp
        onboarding_endpoint.server = onboarding_mcp
        answer_endpoint.server = answer_mcp
        identity_endpoint.server = identity_capability_mcp
        catalogue_endpoint.server = catalogue_capability_mcp
        data_endpoint.server = data_capability_mcp
        assist_endpoint.server = assist_capability_mcp
        search_endpoint.server = search_capability_mcp
        access_endpoint.server = access_capability_mcp
        graph_endpoint.server = graph_capability_mcp
        try:
            async with context_mcp.session_manager.run(), onboarding_mcp.session_manager.run(), answer_mcp.session_manager.run(), identity_capability_mcp.session_manager.run(), catalogue_capability_mcp.session_manager.run(), data_capability_mcp.session_manager.run(), assist_capability_mcp.session_manager.run(), search_capability_mcp.session_manager.run(), access_capability_mcp.session_manager.run(), graph_capability_mcp.session_manager.run():
                yield
        finally:
            BRAND.stop()
            close()
            if pidfile.exists() and pidfile.read_text().strip() == str(os.getpid()):
                pidfile.unlink()
                serving.unlink(missing_ok=True)


def address() -> str | None:
    """The address this process serves on: API_URL, as make serve sets it, else uvicorn's --host and --port on its command
    line (its defaults where one is not given); None when neither says, as under a test client."""
    if (settings().API_URL or "").strip():
        return settings().API_URL.strip()
    argv = sys.argv
    if not argv or "uvicorn" not in Path(argv[0]).as_posix():
        return None
    opts = {"--host": "127.0.0.1", "--port": "8000"}
    for i, a in enumerate(argv):
        for k in opts:
            if a == k and i + 1 < len(argv):
                opts[k] = argv[i + 1]
            elif a.startswith(k + "="):
                opts[k] = a.split("=", 1)[1]
    return f"http://{opts['--host']}:{opts['--port']}"


app = FastAPI(title=product_name(), version=VERSION, lifespan=lifespan, docs_url=None, redoc_url=None,
              telemetry=FASTAPI_TELEMETRY_OFF)


async def swagger_docs(request: Request):
    """Keep the operator's display name as text in the developer reference's HTML title."""
    root = request.scope.get("root_path", "").rstrip("/")
    return get_swagger_ui_html(openapi_url=root + app.openapi_url, title=escape(app.title) + " - Swagger UI",
                               oauth2_redirect_url=root + app.swagger_ui_oauth2_redirect_url)


async def redoc_docs(request: Request):
    """Use the same escaped display name for the alternative API reference."""
    root = request.scope.get("root_path", "").rstrip("/")
    return get_redoc_html(openapi_url=root + app.openapi_url, title=escape(app.title) + " - ReDoc")


async def swagger_redirect(request: Request):
    """Preserve the developer reference's existing OAuth redirect page."""
    return get_swagger_ui_oauth2_redirect_html()


app.add_route("/docs", swagger_docs, methods=["GET"], include_in_schema=False)
app.add_route("/redoc", redoc_docs, methods=["GET"], include_in_schema=False)
app.add_route("/docs/oauth2-redirect", swagger_redirect, methods=["GET"], include_in_schema=False)
for service in (access, onboard, tools, identity, catalogue, data, search, assist, graph, ask, quality, steward):
    app.include_router(service.router)
context_mcp = mcp.mcp_server()
onboarding_mcp = onboard_mcp.mcp_server(mcp.tool)
answer_mcp = mcp.answer_mcp_server()
identity_capability_mcp = identity_mcp.mcp_server()
catalogue_capability_mcp = catalogue_mcp.mcp_server()
data_capability_mcp = data_mcp.mcp_server()
assist_capability_mcp = assist_mcp.mcp_server(mcp.tool)
search_capability_mcp = search_mcp.mcp_server(mcp.tool)
access_capability_mcp = access_mcp.mcp_server()
graph_capability_mcp = graph_mcp.mcp_server(mcp.tool)
context_endpoint = mcp.MCPEndpoint(context_mcp)
onboarding_endpoint = mcp.MCPEndpoint(onboarding_mcp)
answer_endpoint = mcp.MCPEndpoint(answer_mcp)
identity_endpoint = mcp.MCPEndpoint(identity_capability_mcp)
catalogue_endpoint = mcp.MCPEndpoint(catalogue_capability_mcp)
data_endpoint = mcp.MCPEndpoint(data_capability_mcp)
assist_endpoint = mcp.MCPEndpoint(assist_capability_mcp)
search_endpoint = mcp.MCPEndpoint(search_capability_mcp)
access_endpoint = mcp.MCPEndpoint(access_capability_mcp)
graph_endpoint = mcp.MCPEndpoint(graph_capability_mcp)
app.add_route("/mcp", context_endpoint, methods=["GET", "POST", "DELETE"])
app.add_route("/mcp/onboard", onboarding_endpoint, methods=["GET", "POST", "DELETE"])
app.add_route("/mcp/answer", answer_endpoint, methods=["GET", "POST", "DELETE"])
app.add_route("/mcp/identity", identity_endpoint, methods=["GET", "POST", "DELETE"])
app.add_route("/mcp/catalogue", catalogue_endpoint, methods=["GET", "POST", "DELETE"])
app.add_route("/mcp/data", data_endpoint, methods=["GET", "POST", "DELETE"])
app.add_route("/mcp/assist", assist_endpoint, methods=["GET", "POST", "DELETE"])
app.add_route("/mcp/search", search_endpoint, methods=["GET", "POST", "DELETE"])
app.add_route("/mcp/access", access_endpoint, methods=["GET", "POST", "DELETE"])
app.add_route("/mcp/graph", graph_endpoint, methods=["GET", "POST", "DELETE"])


@app.exception_handler(ExternalCallError)
def outside_service_failed(request: Request, exc: ExternalCallError) -> JSONResponse:
    """A call to an outside service failed (the question's embedding on Vertex AI, say, under /ask, /answer, /agent,
    /context or /retrieve): a 503 with the failure's one line, the service's own words, never a 500 and a traceback. The
    request's span records the failure's kind (FAILURE_KIND), a deadline's among them, beside the 503."""
    request.scope[FAILURE_KIND] = type(exc).__name__
    return JSONResponse({"detail": str(exc)}, status_code=503)


def public_without_release(scope) -> bool:
    path = scope.get("path", "")
    return scope.get("method") in {"GET", "HEAD"} and (path in {
        "/health", "/healthz", "/", "/onboarding-guide", "/mode", "/docs", "/redoc",
        "/openapi.json", "/docs/oauth2-redirect"} or path.startswith("/brand/"))


class UnreleasedMiddleware:
    """Refuse unavailable routes before body parsing, identity checks or engine pinning."""

    def __init__(self, app):
        self.app = app
        self.next_release_check = 0.0

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and _serving.get() is None and UNRELEASED_MESSAGE is not None:
            if time.monotonic() >= self.next_release_check:
                self.next_release_check = time.monotonic() + 3
                from kge.services.onboard import jobs
                try:
                    await anyio.to_thread.run_sync(jobs.follow_first_release, sys.modules[__name__])
                except jobs.JobRefused:
                    pass
        if scope["type"] == "http" and _serving.get() is None and not public_without_release(scope):
            try:
                require_released()
            except HTTPException as exc:
                return await JSONResponse({"detail": exc.detail}, status_code=exc.status_code)(scope, receive, send)
        return await self.app(scope, receive, send)


class PinEngineMiddleware:
    """Pure ASGI middleware: a request sees one engine without changing context-task behaviour."""

    def __init__(self, wrapped):
        self.wrapped = wrapped

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or _serving.get() is not None:
            return await self.wrapped(scope, receive, send)
        if public_without_release(scope):
            return await self.wrapped(scope, receive, send)
        with _swap_lock:
            bundle = (engine(), combined(), agent())
            key = id(bundle[0])
            _references[key] = _references.get(key, 0) + 1
        token = _request_bundle.set(bundle)
        try:
            return await self.wrapped(scope, receive, send)
        finally:
            _request_bundle.reset(token)
            closing = None
            with _swap_lock:
                left = _references.get(key, 1) - 1
                if left:
                    _references[key] = left
                else:
                    _references.pop(key, None)
                    closing = _retired.pop(key, None)
            if closing is not None:
                closing.close()


class TracingMiddleware:
    """Pure ASGI middleware: each HTTP request one span, named by its method and the route it matched (the template,
    never the path or the query, which may hold a term or a question), continuing the caller's trace where it sends a
    W3C traceparent (a frontend's), with the response's status. On a question's route (QUESTION_ROUTES) the span ends,
    and the spans ended so far are exported (exported, FLUSH_SECONDS at most), before the response's last part is
    sent, or before a failure goes on to the error response; any other request's spans wait for the batch, so only a
    question can wait on a slow endpoint: Cloud Run gives an instance no CPU once its response is complete, so a batch left for later could
    wait for the next request. The span is handed to the request limits outside it (REQUEST_SPAN, kge/services/http.py):
    where they replace the response with their own, or answer a deadline the app raised before any response started
    (DEADLINE_ERRORS), they record its status on the span, end it and export it, and this leaves it alone. With tracing off the API's span records nothing, and the request passes straight on: no thread, no
    wait. FastAPI's own telemetry is off (FASTAPI_TELEMETRY_OFF, kge/pipeline/telemetry.py), as every library's is."""

    def __init__(self, wrapped):
        self.wrapped = wrapped

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.wrapped(scope, receive, send)
        method = scope.get("method") or "HTTP"
        parent = next((value.decode("latin-1") for key, value in scope.get("headers") or () if key == b"traceparent"), None)
        span = new_span(method, {"http.request.method": method}, parent=parent)
        if not span.is_recording():
            return await self.wrapped(scope, receive, send)
        held = RequestSpan(span, exported)
        scope[REQUEST_SPAN] = held
        status = []
        ended = []

        def named():
            route = served_route(scope)
            span.update_name(f"{method} {route}" if route else method)
            if route:
                span.set_attribute("http.route", route)
            held.question = route in QUESTION_ROUTES

        def finish():
            if ended or scope.get(REQUEST_SPAN) is not held:
                return False
            ended.append(True)
            named()
            if status:
                span.set_attribute("http.response.status_code", status[0])
            if status and status[0] >= 500:
                failed(span, scope.get(FAILURE_KIND) or f"http_{status[0]}")
            span.end()
            return True

        async def sending(message):
            if message["type"] == "http.response.start":
                status.append(message["status"])
                named()
            if (message["type"] == "http.response.body" and not message.get("more_body", False) and finish()
                    and held.question):
                await exported()
            await send(message)
        try:
            with as_current(span):
                return await self.wrapped(scope, receive, sending)
        except BaseException as exc:
            if isinstance(exc, DEADLINE_ERRORS) and not status and scope.get(REQUEST_SPAN) is held:
                named()
                ended.append(True)
                raise
            if not ended and scope.get(REQUEST_SPAN) is held:
                failed(span, type(exc).__name__)
            if finish() and held.question:
                with anyio.CancelScope(shield=True):
                    await exported()
            raise
        finally:
            finish()


async def exported() -> None:
    """Wait for the spans ended so far to be exported, FLUSH_SECONDS at most, without holding a worker thread: the
    flush runs on its own thread and this polls its event."""
    done = flush()
    deadline = time.monotonic() + FLUSH_SECONDS
    pause = 0.002
    while not done.is_set() and time.monotonic() < deadline:
        await anyio.sleep(min(pause, max(0.0, deadline - time.monotonic())))
        pause = min(pause * 2, 0.02)


QUESTION_ROUTES = frozenset({"/ask", "/answer", "/agent", "/assist", "/context", "/ask/runs/{run_id}", "/mcp", "/mcp/answer",
                             "/mcp/assist"})


def served_route(scope) -> str | None:
    """The template of the route a request matched (an MCP server's among them), else None."""
    route = scope.get("route")
    if getattr(route, "path", None):
        return route.path
    endpoint = scope.get("endpoint")
    if endpoint is None:
        return None
    return next((candidate.path for candidate in app.routes if getattr(candidate, "endpoint", None) is endpoint), None)


app.add_middleware(PinEngineMiddleware)
app.add_middleware(UnreleasedMiddleware)
app.add_middleware(TracingMiddleware)
app.add_middleware(RequestLimitsMiddleware)
app.add_exception_handler(RequestValidationError, request_validation_failed)
