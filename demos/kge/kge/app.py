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
/context (in this process unless CONTEXT_URL names another service, serving);
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
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from kge.services.ask.engine.agent import Agent
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.context import as_json as as_json
from kge.services.http import principal as principal
from kge.services.http import RequestLimitsMiddleware, in_process_hop, request_validation_failed
from kge.services.ask.engine.core import Engine
from kge.services.identity.service import Identities, SignedSessions, Throttle, session_cookie
from kge.services.ask import mcp, routes as ask
from kge.services.identity import routes as identity
from kge.services.identity import mcp as identity_mcp
from kge.services.catalogue import routes as catalogue
from kge.services.catalogue import mcp as catalogue_mcp
from kge.services.data import routes as data
from kge.services.data import mcp as data_mcp
from kge.services.search import routes as search
from kge.services.search import mcp as search_mcp
from kge.services.onboard import routes as onboard
from kge.services.onboard import mcp as onboard_mcp
from kge.services.quality import routes as quality
from kge.services.steward import routes as steward
from kge.services.tools import routes as tools
from kge.pipeline.envfile import load_env
from kge.adapters.brand import Brand, BrandSettingError
from kge.adapters.caller import TrustedCallerSettingError, trusted_caller
from kge.adapters.model import ExternalCallError, ModelSettingError, check_settings
from kge.pipeline.profile import resolve
from kge.pipeline.settings import check_service_auth_settings, settings

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
_combined: Combined | None = None
_agent: Agent | None = None
_ids: Identities | None = None
_serving: ContextVar[Agent | None] = ContextVar("serving", default=None)
_request_bundle: ContextVar[tuple[Engine, Combined, Agent] | None] = ContextVar("request_bundle", default=None)
_swap_lock = threading.RLock()
_references: dict[int, int] = {}
_retired: dict[int, Engine] = {}
SIGN_IN = "sign in: POST /session with a token, or send Authorization: Bearer <token> (python3 -m kge.services.identity.cli prints the demo tokens)"


def engine() -> Engine:
    global _engine
    if _serving.get() is not None:
        return _serving.get().e
    if _request_bundle.get() is not None:
        return _request_bundle.get()[0]
    if _engine is None:
        _engine = Engine.serving(PROFILE)
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
        _ids = Identities(PROFILE.run_dir(), engine().g.principal_ids(),
                          SignedSessions(PROFILE.run_dir(), PROFILE.id) if PASSWORDS else None)
    return _ids


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
    global _engine, _combined, _agent
    fresh_engine = Engine.serving(PROFILE)
    fresh_combined = Combined(fresh_engine)
    fresh_agent = Agent(fresh_engine)
    closing = None
    with _swap_lock:
        old = _engine
        _engine, _combined, _agent = fresh_engine, fresh_combined, fresh_agent
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
    """The app's life: its title is the client's, as its pack titles it (this code names none); it writes its pid and
    address to the run state, so a locked-graph message can name this process and the way round it; and it runs /mcp's
    session manager for as long as the app serves."""
    _app.title = engine().TITLE or _app.title
    ids()
    BRAND.start()
    pidfile = PROFILE.run_dir() / "api.pid"
    serving = PROFILE.run_dir() / "api.json"
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text(str(os.getpid()))
    serving.write_text(json.dumps({"pid": os.getpid(), "profile": PROFILE.id, "url": address()}) + "\n")
    global context_mcp, onboarding_mcp, answer_mcp, identity_capability_mcp, catalogue_capability_mcp, data_capability_mcp, search_capability_mcp
    context_mcp = mcp.mcp_server()
    onboarding_mcp = onboard_mcp.mcp_server(mcp.tool)
    answer_mcp = mcp.answer_mcp_server()
    identity_capability_mcp = identity_mcp.mcp_server()
    catalogue_capability_mcp = catalogue_mcp.mcp_server()
    data_capability_mcp = data_mcp.mcp_server()
    search_capability_mcp = search_mcp.mcp_server(mcp.tool)
    context_endpoint.server = context_mcp
    onboarding_endpoint.server = onboarding_mcp
    answer_endpoint.server = answer_mcp
    identity_endpoint.server = identity_capability_mcp
    catalogue_endpoint.server = catalogue_capability_mcp
    data_endpoint.server = data_capability_mcp
    search_endpoint.server = search_capability_mcp
    try:
        async with context_mcp.session_manager.run(), onboarding_mcp.session_manager.run(), answer_mcp.session_manager.run(), identity_capability_mcp.session_manager.run(), catalogue_capability_mcp.session_manager.run(), data_capability_mcp.session_manager.run(), search_capability_mcp.session_manager.run():
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


app = FastAPI(title="Governance graph", version=VERSION, lifespan=lifespan)
for service in (onboard, tools, identity, catalogue, data, search, ask, quality, steward):
    app.include_router(service.router)
context_mcp = mcp.mcp_server()
onboarding_mcp = onboard_mcp.mcp_server(mcp.tool)
answer_mcp = mcp.answer_mcp_server()
identity_capability_mcp = identity_mcp.mcp_server()
catalogue_capability_mcp = catalogue_mcp.mcp_server()
data_capability_mcp = data_mcp.mcp_server()
search_capability_mcp = search_mcp.mcp_server(mcp.tool)
context_endpoint = mcp.MCPEndpoint(context_mcp)
onboarding_endpoint = mcp.MCPEndpoint(onboarding_mcp)
answer_endpoint = mcp.MCPEndpoint(answer_mcp)
identity_endpoint = mcp.MCPEndpoint(identity_capability_mcp)
catalogue_endpoint = mcp.MCPEndpoint(catalogue_capability_mcp)
data_endpoint = mcp.MCPEndpoint(data_capability_mcp)
search_endpoint = mcp.MCPEndpoint(search_capability_mcp)
app.add_route("/mcp", context_endpoint, methods=["GET", "POST", "DELETE"])
app.add_route("/mcp/onboard", onboarding_endpoint, methods=["GET", "POST", "DELETE"])
app.add_route("/mcp/answer", answer_endpoint, methods=["GET", "POST", "DELETE"])
app.add_route("/mcp/identity", identity_endpoint, methods=["GET", "POST", "DELETE"])
app.add_route("/mcp/catalogue", catalogue_endpoint, methods=["GET", "POST", "DELETE"])
app.add_route("/mcp/data", data_endpoint, methods=["GET", "POST", "DELETE"])
app.add_route("/mcp/search", search_endpoint, methods=["GET", "POST", "DELETE"])


@app.exception_handler(ExternalCallError)
def outside_service_failed(request: Request, exc: ExternalCallError) -> JSONResponse:
    """A call to an outside service failed (the question's embedding on Vertex AI, say, under /ask, /answer, /agent,
    /context or /retrieve): a 503 with the failure's one line, the service's own words, never a 500 and a traceback."""
    return JSONResponse({"detail": str(exc)}, status_code=503)


class PinEngineMiddleware:
    """Pure ASGI middleware: a request sees one engine without changing context-task behaviour."""

    def __init__(self, wrapped):
        self.wrapped = wrapped

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") in ("/health", "/healthz") or _serving.get() is not None:
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


app.add_middleware(PinEngineMiddleware)
app.add_middleware(RequestLimitsMiddleware)
app.add_exception_handler(RequestValidationError, request_validation_failed)
