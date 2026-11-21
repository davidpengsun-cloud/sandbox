"""What every service's routes share: the app's state, read as each request is served, the request bodies' base,
and sign-in."""
from __future__ import annotations

import hmac
import importlib
import json
import re
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Awaitable, Callable
from threading import Lock

import anyio
import jsonschema
from fastapi import Depends, HTTPException, Request
from mcp import types as mcp_types
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.tools import ToolManager
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, ConfigDict
from pydantic import ValidationError

from kge.adapters.deadline import ExternalCallError, RequestDeadlineExceeded, request_remaining, request_scope
from kge.adapters.appstate import StateUnavailable
from kge.adapters.roleaudit import role_audit_log
from kge.pipeline import access_table
from kge.pipeline.settings import settings
from kge.pipeline.trace import failed
from kge.pipeline.access import ACCESS_ADMIN, CEREBRO_MATRIX, cerebro_roles, matrix_allows
from kge.ports import RoleAuditEntry, StoreError


MAX_REQUEST_BYTES = 4 * 1024 * 1024
BODY_TOO_LARGE = f"Request body must be at most {MAX_REQUEST_BYTES} bytes."
MAX_IN_FLIGHT = 4
TOO_MANY_REQUESTS = "Too many requests in flight for this person; wait for one to finish and try again."
_IN_FLIGHT: dict[str, int] = {}
_IN_FLIGHT_LOCK = Lock()
_IN_PROCESS_HOP: ContextVar[bool] = ContextVar("kge_in_process_hop", default=False)
IN_PROCESS_CLIENT = ("kge-in-process", 0)
TOOL_LIMITS: dict[tuple[str, str], type[BaseModel]] = {}
READ_POST_ROUTES = {"/assist", "/ask", "/answer", "/agent", "/context", "/retrieve", "/search",
                    "/data/query", "/data/preview", "/data/export", "/sql/check", "/eval"}


REQUEST_SPAN = "kge.request_span"
FAILURE_KIND = "kge.failure_kind"
"""Where an exception handler that answers with its own error status names the failure's kind (an exception's type
name), for the request's span to record in place of the bare status (kge/app.py TracingMiddleware)."""
DEADLINE_ERRORS = (RequestDeadlineExceeded, TimeoutError)
"""What the request limits answer with their own 503 when the app raises it before any response has started; the
tracing middleware leaves the request's span to them for it (kge/app.py TracingMiddleware)."""


class RequestSpan:
    """The request's span (OpenTelemetry's), which the tracing middleware (kge/app.py) puts in the request's scope under
    REQUEST_SPAN while it records one, and nothing with tracing off: whoever takes it from the scope ends it, and the
    tracing middleware then leaves it alone. question: the request matched a question's route, whose spans exported()
    sends, FLUSH_SECONDS at most, before its response completes."""

    def __init__(self, span, exported: Callable[[], Awaitable[None]]):
        self.span = span
        self.exported = exported
        self.question = False


async def replaced_in_trace(scope, status: int, kind: str) -> None:
    """The limits' own response replaces the app's, or stands in for one the app never sent (DEADLINE_ERRORS): the
    request's span, where tracing handed one over, records status and
    the kind of failure in place of the app's status, ends, and on a question's route is exported before the replacement
    is sent. With tracing off there is none, and nothing is done."""
    held = scope.pop(REQUEST_SPAN, None)
    if held is None:
        return
    held.span.set_attribute("http.response.status_code", status)
    failed(held.span, kind)
    held.span.end()
    if held.question:
        await held.exported()


def work_route(method: str, path: str) -> bool:
    """Count query POSTs and onboarding or steward mutations, never identity or status reads."""
    return ((method == "POST" and path in READ_POST_ROUTES)
            or (method in {"POST", "PUT", "PATCH", "DELETE"}
                and path.startswith(("/onboard/", "/steward/"))))


def read_route(method: str, path: str) -> bool:
    return method in {"GET", "HEAD"} or (method == "POST" and path in READ_POST_ROUTES)


@contextmanager
def in_process_hop():
    """A call the service makes to its own app in this process is not a person's request and takes no slot."""
    token = _IN_PROCESS_HOP.set(True)
    try:
        yield
    finally:
        _IN_PROCESS_HOP.reset(token)


def in_process(app):
    """app as this API's own call to itself reaches it, each call an in-process hop (in_process_hop): the person's
    request that makes it holds the person's slot, so the call takes no second one. The flag is this process's
    contextvar, set here alone; nothing a request carries sets it. Its client is IN_PROCESS_CLIENT, never a loopback
    address, so no rule for a caller on this machine matches it."""
    async def hop(scope, receive, send):
        with in_process_hop():
            await app({**scope, "client": IN_PROCESS_CLIENT}, receive, send)
    return hop


@contextmanager
def person_slot(who: str):
    """Share a process-wide allowance across HTTP requests and MCP tool calls; always release it. An in-process hop
    (in_process) takes none: the request that made it holds the person's."""
    if _IN_PROCESS_HOP.get():
        yield
        return
    with _IN_FLIGHT_LOCK:
        if _IN_FLIGHT.get(who, 0) >= MAX_IN_FLIGHT:
            raise HTTPException(429, TOO_MANY_REQUESTS, headers={"Retry-After": "1"})
        _IN_FLIGHT[who] = _IN_FLIGHT.get(who, 0) + 1
    try:
        yield
    finally:
        with _IN_FLIGHT_LOCK:
            left = _IN_FLIGHT[who] - 1
            if left:
                _IN_FLIGHT[who] = left
            else:
                del _IN_FLIGHT[who]


def tool_limits(server: str, **models: type[BaseModel]) -> None:
    """Routes supply their transport-neutral limit models without shared HTTP code importing a service."""
    TOOL_LIMITS.update({(server, name): model for name, model in models.items()})


def question_limit_detail(errors) -> str | None:
    for error in errors:
        if error["type"] == "string_too_long" and error["loc"][-1:] == ("question",):
            return f"Question must be at most {error['ctx']['max_length']} characters."
    return None


def validation_detail(errors) -> str | list[dict]:
    """Only field locations and safe messages leave validation; never input or context values."""
    if detail := question_limit_detail(errors):
        return detail
    return [{"loc": [str(part).encode("utf-8", "backslashreplace").decode() if isinstance(part, str) else part
                     for part in error["loc"]],
             "msg": error["msg"].encode("utf-8", "backslashreplace").decode()}
            for error in errors]


def unicode_errors(value, loc=("body",)) -> list[dict]:
    """Walk bounded JSON iteratively; reject excessive nesting and lone surrogates."""
    pending = [(value, loc)]
    errors = []
    while pending:
        value, at = pending.pop()
        if len(at) - len(loc) > 256:
            return [{"type": "json_depth", "loc": loc, "msg": "JSON body is nested too deeply."}]
        if isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                errors.append({"type": "string_unicode", "loc": at, "msg": "Input must be valid Unicode text."})
        elif isinstance(value, dict):
            for key, child in reversed(value.items()):
                pending.extend(((child, (*at, key)), (key, (*at, key))))
        elif isinstance(value, list):
            pending.extend((child, (*at, i)) for i, child in reversed(list(enumerate(value))))
    return errors


async def request_validation_failed(request: Request, exc: RequestValidationError):
    """Every route returns field locations and messages without echoing credentials or other inputs."""
    return JSONResponse({"detail": validation_detail(exc.errors())}, status_code=422)


class RequestLimitsMiddleware:
    """Bound every HTTP body before parsing and share a clock with all synchronous request work.

    Workers finish while holding their engine lease; only the adapters abandon remote I/O.
    A late read is refused before headers leave; completed writes keep their result.
    MCP applies the same rule using each tool's route metadata.
    A 202 acknowledges a subprocess job, whose lifetime is independent of this clock.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        slots: dict = {}
        if not _IN_PROCESS_HOP.get():
            scope["kge.person_slots"] = slots
        started = False
        refused = False
        is_mcp = scope.get("path", "").split("/")[1:2] == ["mcp"]

        async def bounded_send(message):
            nonlocal started, refused
            if refused:
                return
            if message["type"] == "http.response.start":
                if (not is_mcp and read_route(scope.get("method", ""), scope.get("path", ""))
                        and 200 <= message["status"] < 300 and message["status"] != 202):
                    try:
                        request_remaining()
                    except RequestDeadlineExceeded as exc:
                        refused = started = True
                        await replaced_in_trace(scope, 503, type(exc).__name__)
                        await JSONResponse({"detail": str(exc)}, status_code=503)(scope, receive, send)
                        return
                started = True
            await send(message)

        try:
            with request_scope():
                for name, value in scope.get("headers", []):
                    if name.lower() == b"content-length" and value.isdigit() and int(value) > MAX_REQUEST_BYTES:
                        return await JSONResponse({"detail": BODY_TOO_LARGE}, status_code=413)(scope, receive, send)
                body = bytearray()
                while True:
                    with anyio.fail_after(request_remaining()):
                        message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > MAX_REQUEST_BYTES:
                        return await JSONResponse({"detail": BODY_TOO_LARGE}, status_code=413)(scope, receive, send)
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
                if not is_mcp and work_route(scope.get("method", ""), scope.get("path", "")):
                    headers = {name.lower() for name, _ in scope.get("headers", [])}
                    if headers & {b"authorization", b"x-kge-authorization", b"cookie"}:
                        try:
                            await anyio.to_thread.run_sync(principal, Request(scope))
                        except HTTPException as exc:
                            if exc.status_code != 401:
                                return await JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)(scope, receive, send)
                if body:
                    try:
                        parsed = json.loads(body)
                    except RecursionError:
                        return await JSONResponse({"detail": "JSON body is nested too deeply."}, status_code=422)(scope, receive, send)
                    except (ValueError, UnicodeError):
                        pass
                    else:
                        errors = unicode_errors(parsed)
                        if is_mcp:
                            errors = [error for error in errors if error["type"] == "json_depth"]
                        if errors:
                            return await JSONResponse({"detail": validation_detail(errors)}, status_code=422)(scope, receive, send)
                replayed = False

                async def replay():
                    nonlocal replayed
                    if replayed:
                        return await receive()
                    replayed = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}

                await self.app(scope, replay, bounded_send)
        except DEADLINE_ERRORS:
            if started:
                raise
            await replaced_in_trace(scope, 503, RequestDeadlineExceeded.__name__)
            await JSONResponse({"detail": str(RequestDeadlineExceeded())}, status_code=503)(scope, receive, send)
        finally:
            for slot in slots.values():
                slot.__exit__(None, None, None)


class Served:
    """kge.app, the module, read at each use: so a route reads the state the app holds (its engine, its identities, its
    profile) as it is when the request is served, and never imports the app while the app is importing it."""

    def __getattr__(self, name: str):
        return getattr(importlib.import_module("kge.app"), name)


served = Served()


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


def require_released() -> None:
    """The same unavailable-service refusal for HTTP identities and MCP tools."""
    if message := getattr(served, "UNRELEASED_MESSAGE", None):
        raise HTTPException(503, message)


def trusted_portal(request: Request, refused: str) -> None:
    """Refuse (403, refused) a caller that is not the co-deployed portal service: its Google ID token in X-Caller-Token
    that served.TRUSTED_CALLER verifies, where TRUSTED_CALLER_AUDIENCE is set (the pilot's); else PORTAL_SYNC_TOKEN in
    X-Portal-Sync-Token (this machine, Docker Compose); with neither set, a 503."""
    current = settings()
    caller_token = request.headers.get("x-caller-token", "").strip()
    if current.TRUSTED_CALLER_AUDIENCE:
        authenticated = bool(caller_token and served.TRUSTED_CALLER.trusts(caller_token))
    elif current.PORTAL_SYNC_TOKEN:
        supplied = request.headers.get("x-portal-sync-token", "")
        try:
            authenticated = hmac.compare_digest(supplied.encode("utf-8"), current.PORTAL_SYNC_TOKEN.encode("utf-8"))
        except UnicodeError:
            authenticated = False
    else:
        raise HTTPException(503, "the portal sync is not configured")
    if not authenticated:
        raise HTTPException(403, refused)


def principal(request: Request) -> str:
    require_released()
    cached = request.scope.get("kge.principal") if "kge.person_slots" in request.scope else None
    if cached is not None:
        return cached
    auth_header = request.headers.get("x-kge-authorization") or request.headers.get("authorization")
    who = served.ids().resolve(auth_header, request.cookies.get(served.COOKIE))
    if who is None:
        raise HTTPException(401, served.SIGN_IN)
    slots = request.scope.get("kge.person_slots")
    if slots is not None:
        request.scope["kge.principal"] = who
        if who not in slots and work_route(request.method, request.url.path):
            slot = person_slot(who)
            slot.__enter__()
            slots[who] = slot
    return who


def person_bearer(request: Request) -> str:
    """The signed-in person's own bearer, for a call the service makes on the person's behalf: the one the request
    carries (X-KGE-Authorization, else Authorization), else the session cookie's own short-lived bearer. A 401 where
    there is neither, as the sign-in check's."""
    header = request.headers.get("x-kge-authorization") or request.headers.get("authorization") or ""
    if header.lower().startswith("bearer ") and header[7:].strip():
        return header[7:].strip()
    session = served.ids().session_bearer(request.cookies.get(served.COOKIE))
    if session is None:
        raise HTTPException(401, served.SIGN_IN)
    return session["access_token"]


def mcp_principal(request: Request | None) -> str:
    """Resolve a local token, or a password- or session-exchanged bearer; MCP never takes a browser cookie."""
    require_released()
    auth_header = (request.headers.get("x-kge-authorization") or request.headers.get("authorization")) if request else None
    who = served.ids().resolve(auth_header, None)
    if who is None:
        raise HTTPException(401, served.SIGN_IN)
    return who


def mcp_subject(request: Request | None) -> str:
    try:
        return mcp_principal(request)
    except HTTPException as exc:
        raise ToolError(f"{exc.status_code}: {exc.detail}") from None


def may(who: str, process: str) -> str:
    if not served.engine().g.may(who, process):
        raise HTTPException(403, f"{who} is not {'an' if process[:1] in 'aeiou' else 'a'} {process} identity")
    return who


def cerebro_held(who: str, state=None) -> list[str]:
    """The person's Cerebro roles, from the same deployment roles /whoami maps (kge/pipeline/access.py cerebro_roles):
    the roles the person acts in, each read from the served release's roles, and the access administrator's reserved
    role where the access table names them one, as /whoami adds it, so an access administrator's audit entry says
    admin as /whoami does. Held in memory: the access table's reader refreshes at most once a minute. state: the app
    whose graph is read, the served one by default."""
    graph = (state or served).engine().g
    administers = {ACCESS_ADMIN} if graph.people.is_admin(who) else set()
    return cerebro_roles(graph.acting_roles(who) | administers, graph.role_templates())


def engineer_view(who: str) -> bool:
    """Whether the role matrix gives who plan, SQL and trace (row 7): an engineer alone. Every reply anyone else gets is
    compact, whichever service answers."""
    return "engineer" in cerebro_held(who)


def _role_audit(state, who: str, held: list[str], row: str, endpoint: str, outcome: str) -> None:
    entry = RoleAuditEntry(time=datetime.now(timezone.utc).isoformat(timespec="seconds"), who=who, cerebro_roles=tuple(held),
                           row=row, endpoint=endpoint, outcome=outcome)
    try:
        role_audit_log(state.PROFILE.run_dir(), state.PROFILE.client).append(entry)
    except (StoreError, OSError) as exc:
        raise HTTPException(503, f"the role audit is unavailable: {exc}") from None


def role_check(who: str, row: str, endpoint: str, today: Callable[[str], object], state=None) -> str:
    """The role matrix's check (cerebro.md §8.1) in front of today's check, one role-audit entry (§8.3) for each call it
    checks, allowed or refused. Where none of the person's Cerebro roles has a tick in the row: the matrix's 403 and a
    refused entry, before today's check runs. Where one has: today's check; its 403 is a refused entry and the same 403,
    and the allowed entry is written only once it passes, so an audited allow is a real one. Any other error of today's
    check (a 503 from a store it reads, a 409) is no decision and leaves no entry. The matrix only narrows: today's check
    still refuses what it refused. state: the app whose graph and profile are read, the served one unless a service's
    routes read theirs through an accessor of their own (the onboard service's _app)."""
    state = served if state is None else state
    held = cerebro_held(who, state)
    if not matrix_allows(held, row):
        _role_audit(state, who, held, row, endpoint, "refused")
        raise HTTPException(403, f"{who} may not do this: {CEREBRO_MATRIX[row][0]} is for {', '.join(CEREBRO_MATRIX[row][1])}")
    try:
        today(who)
    except HTTPException as exc:
        if exc.status_code == 403:
            _role_audit(state, who, held, row, endpoint, "refused")
        raise
    _role_audit(state, who, held, row, endpoint, "allowed")
    return who


def gate(request: Request | None, who: str, row: str, today: Callable[[str], object], state=None) -> str:
    """A route dependency's check: the role matrix's row in front of today's, the audit naming the route the request
    matched (its method and path template). Called with a person alone (no request), as a /run step's check or an MCP
    tool's is, today's check alone: its caller applies the row."""
    if request is None:
        today(who)
        return who
    route = request.scope.get("route")
    return role_check(who, row, f"{request.method} {getattr(route, 'path', request.url.path)}", today, state)


def matrix(row: str, process: str = "querying"):
    """The route's dependency: the role matrix's row in front of today's process check."""
    def dep(who: str = Depends(principal), request: Request = None) -> str:
        return gate(request, who, row, lambda person: may(person, process))
    return dep


def tool_matrix(row: str, tool_name: str, process: str = "querying"):
    """An MCP tool's check: the role matrix's row in front of today's process check, the same as its route's."""
    def check(who: str) -> str:
        return role_check(who, row, f"mcp {tool_name}", lambda person: may(person, process))
    return check


def needs(process: str):
    def dep(who: str = Depends(principal)) -> str:
        return may(who, process)
    return dep


def admin(who: str = Depends(principal)) -> str:
    """The same fresh administrator check for HTTP and MCP, independent of pack roles."""
    try:
        allowed = access_table.is_admin(served.PROFILE, who)
    except (StateUnavailable, StoreError) as exc:
        raise HTTPException(503, str(exc)) from None
    if not allowed:
        raise HTTPException(403, f"{who} is not an admin (access ADMINS or ACCESS_ADMINS)")
    return who


def demo_allowed(request: Request) -> bool:
    """Demo mode requires both a loopback caller and a loopback Host, preventing DNS rebinding."""
    hosts = request.headers.getlist("host")
    return bool(served.DEMO_MODE and request.client is not None and request.client.host in served.LOOPBACK
                and len(hosts) == 1
                and re.fullmatch(r"(?:127\.0\.0\.1|localhost|\[::1\])(?::[0-9]+)?", hosts[0], re.IGNORECASE))


def mcp_server(name: str, checks: dict | None = None) -> FastMCP:
    """Build one stateless MCP capability server with the API's transport settings. The SDK's Host check is off so a
    deployment host such as Cloud Run's is admitted: MCPEndpoint requires a bearer and never accepts a cookie, so browser
    DNS rebinding cannot authenticate a request to these mounts."""
    server = FastMCP(name, stateless_http=True, json_response=True, log_level="WARNING",
                     transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    server._tool_manager = StrictToolManager(name, checks)
    server.streamable_http_app()
    return server


class StrictToolManager(ToolManager):
    """MCP tools whose wire arguments follow the same closed models and validation detail as HTTP bodies. A tool's
    structured result is checked against its output schema here, by a validator compiled once as the tool is added,
    and handed on as the call's result: the SDK's own check compiles the schema again on every call, on the event
    loop (a tenth of a second and more for ask's), so it is not left to make."""

    def __init__(self, server: str, checks: dict | None = None):
        super().__init__()
        self.server = server
        self.checks = checks or {}
        self.outputs: dict[str, jsonschema.protocols.Validator] = {}

    def add_tool(self, *args, **kwargs):
        registered = super().add_tool(*args, **kwargs)
        model = registered.fn_metadata.arg_model
        limits = TOOL_LIMITS.get((self.server, registered.name))
        if limits is not None:
            for name in limits.model_fields:
                if name in model.model_fields and name in limits.model_fields:
                    model.model_fields[name].metadata = list(limits.model_fields[name].metadata)
        model.model_config = ConfigDict(**{**model.model_config, "extra": "forbid"})
        model.model_rebuild(force=True)
        registered.parameters = model.model_json_schema(by_alias=True)
        if registered.output_schema is not None:
            kind = jsonschema.validators.validator_for(registered.output_schema)
            kind.check_schema(registered.output_schema)
            self.outputs[registered.name] = kind(registered.output_schema)
        return registered

    def checked(self, name: str, out):
        """out, a converted tool result, as the call's result once its structured content passes the tool's output
        schema: the SDK's own result for it, content and structured content; out as it is for a tool with no schema."""
        validator = self.outputs.get(name)
        if validator is None or not isinstance(out, tuple) or len(out) != 2:
            return out
        content, structured = out
        if structured is None:
            raise ToolError("Output validation error: outputSchema defined but no structured output returned")
        try:
            validator.validate(structured)
        except jsonschema.ValidationError as exc:
            raise ToolError(f"Output validation error: {exc.message}") from None
        return mcp_types.CallToolResult(content=list(content), structuredContent=structured, isError=False)

    async def call_tool(self, name, arguments, context=None, convert_result=False):
        try:
            with request_scope():
                out = await self._bounded_tool(name, arguments, context, convert_result)
                if convert_result:
                    out = self.checked(name, out)
                registered = self.get_tool(name)
                route = (registered.meta or {}).get("route", "").split(" ", 1)
                if len(route) == 2 and read_route(*route):
                    request_remaining()
                return out
        except ExternalCallError as exc:
            raise ToolError(f"503: {exc}") from None
        except ToolError as exc:
            if isinstance(exc.__cause__, RequestDeadlineExceeded):
                raise ToolError(f"503: {exc.__cause__}") from None
            raise

    async def _bounded_tool(self, name, arguments, context, convert_result):
        registered = self.get_tool(name)
        if registered is None:
            raise ToolError(f"Unknown tool: {name}")
        request = context.request_context.request if context else None

        try:
            who = await anyio.to_thread.run_sync(mcp_principal, request)
            route = (registered.meta or {}).get("route", "").split(" ", 1)
            with person_slot(who) if len(route) == 2 and work_route(*route) else nullcontext():
                if check := self.checks.get(name):
                    await anyio.to_thread.run_sync(check, who)
                if errors := unicode_errors(arguments):
                    raise ToolError(f"422: {validation_detail(errors)}")
                try:
                    registered.fn_metadata.arg_model.model_validate(arguments)
                except ValidationError as exc:
                    errors = [{**error, "loc": ("body", *error["loc"])} for error in exc.errors()]
                    raise ToolError(f"422: {validation_detail(errors)}") from None
                return await registered.run(arguments, context=context, convert_result=convert_result)
        except HTTPException as exc:
            raise ToolError(f"{exc.status_code}: {exc.detail}") from None
