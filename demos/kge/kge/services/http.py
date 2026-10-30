"""What every service's routes share: the app's state, read as each request is served, the request bodies' base,
and sign-in."""
from __future__ import annotations

import importlib
import json
import re
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from threading import Lock

import anyio
from fastapi import Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.tools import ToolManager
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, ConfigDict
from pydantic import ValidationError

from kge.adapters.deadline import ExternalCallError, RequestDeadlineExceeded, request_remaining, request_scope


MAX_REQUEST_BYTES = 4 * 1024 * 1024
BODY_TOO_LARGE = f"Request body must be at most {MAX_REQUEST_BYTES} bytes."
MAX_IN_FLIGHT = 4
TOO_MANY_REQUESTS = "Too many requests in flight for this person; wait for one to finish and try again."
_IN_FLIGHT: dict[str, int] = {}
_IN_FLIGHT_LOCK = Lock()
_IN_PROCESS_HOP: ContextVar[bool] = ContextVar("kge_in_process_hop", default=False)
TOOL_LIMITS: dict[tuple[str, str], type[BaseModel]] = {}
READ_POST_ROUTES = {"/ask", "/answer", "/agent", "/context", "/retrieve", "/search",
                    "/data/query", "/data/preview", "/data/export", "/sql/check", "/eval"}


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


@contextmanager
def person_slot(who: str):
    """Share a process-wide allowance across HTTP requests and MCP tool calls; always release it."""
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
        except (RequestDeadlineExceeded, TimeoutError):
            if started:
                raise
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


def principal(request: Request) -> str:
    auth_header = request.headers.get("x-kge-authorization") or request.headers.get("authorization")
    who = served.ids().resolve(auth_header, request.cookies.get(served.COOKIE))
    if who is None:
        raise HTTPException(401, served.SIGN_IN)
    slots = request.scope.get("kge.person_slots")
    if slots is not None and who not in slots and work_route(request.method, request.url.path):
        slot = person_slot(who)
        slot.__enter__()
        slots[who] = slot
    return who


def mcp_principal(request: Request | None) -> str:
    """Resolve a local token or a password-exchanged signed bearer; MCP never takes a browser cookie."""
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


def needs(process: str):
    def dep(who: str = Depends(principal)) -> str:
        return may(who, process)
    return dep


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
    """MCP tools whose wire arguments follow the same closed models and validation detail as HTTP bodies."""

    def __init__(self, server: str, checks: dict | None = None):
        super().__init__()
        self.server = server
        self.checks = checks or {}

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
        return registered

    async def call_tool(self, name, arguments, context=None, convert_result=False):
        try:
            with request_scope():
                out = await self._bounded_tool(name, arguments, context, convert_result)
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
