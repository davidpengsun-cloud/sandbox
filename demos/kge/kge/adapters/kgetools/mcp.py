"""McpKgeTools: the KgeTools port (kge/ports.py) over KGE's own MCP tools, get_context on /mcp and ask on /mcp/answer,
called with the mcp SDK's client (ClientSession over streamable_http_client) as the person whose bearer token it was
opened with. Each call opens its own session: the servers are stateless.

An ask given on_event asks the tool for progress, so the server answers it as an event stream and sends each pipeline
event as a progress notification whose message is the event's JSON and whose progress counts the events; the adapter
hands each to on_event in that count's order, once, before the answer returns. Without on_event the call is the plain
JSON one any MCP client makes.

A tool refusal is the endpoint's own status and detail ("<status>: <detail>"): a 503 is an ExternalCallError, as the
API's own, and any other a ToolRefused carrying the status (SignInRefused for 401, AccessRefused for 403). A server that
does not answer is an ExternalCallError, transient."""
from __future__ import annotations

import json
import re
from typing import Any, Callable

import anyio
import httpx

from kge.adapters.deadline import ExternalCallError

CONTEXT_MOUNT = "/mcp"
ANSWER_MOUNT = "/mcp/answer"
REFUSAL = re.compile(r"^(?:Error executing tool [^:]+: )?(\d{3}): (.*)$", re.S)
TIMEOUT_SECONDS = 300.0


class ToolRefused(RuntimeError):
    """A KGE tool refused the call: status, the endpoint's HTTP status (None where the tool gave none), and its detail."""

    def __init__(self, status: int | None, detail: str):
        super().__init__(f"{status}: {detail}" if status is not None else detail)
        self.status = status
        self.detail = detail


class SignInRefused(ToolRefused):
    """401: the bearer token names no one KGE knows."""


class AccessRefused(ToolRefused):
    """403: the person may not use the tool."""


def refused(status: int | None, detail: str) -> Exception:
    """The typed error for a refusal with status and detail."""
    if status == 503:
        return ExternalCallError(detail, transient=True)
    kind = {401: SignInRefused, 403: AccessRefused}.get(status, ToolRefused)
    return kind(status, detail)


def as_json(value: Any) -> Any:
    """A model (a history turn, a stages override) as the JSON its tool argument takes; anything else as given."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_unset=True)
    return value


class McpKgeTools:
    """KGE's question tools at url as the person bearer names. app, an ASGI app, takes the calls in this process
    instead of over the network; loop, an anyio EventLoopToken, runs them on that event loop (the one serving app,
    whose MCP session managers run there), from any thread; without one each call runs on an event loop of its own,
    which suits a caller with none running."""

    def __init__(self, url: str, bearer: str, *, app=None, loop=None, timeout: float = TIMEOUT_SECONDS):
        self.url = url.rstrip("/")
        self.bearer = bearer
        self.app = app
        self.loop = loop
        self.timeout = timeout

    def get_context(self, question: str, *, purpose: str, arm: str, products: list[str] | None,
                    plan_vocabulary: bool, history, runtimes: list[str] | None) -> dict:
        """The get_context tool's package (POST /context's)."""
        return self.call(CONTEXT_MOUNT, "get_context", {
            "question": question, "purpose": purpose, "arm": arm, "products": products,
            "plan_vocabulary": plan_vocabulary, "history": [as_json(h) for h in history or ()], "runtimes": runtimes})

    def ask(self, question: str, *, purpose: str, arm: str, stages, products: list[str] | None, history,
            runtimes: list[str] | None, on_event: Callable[[dict], None] | None = None) -> dict:
        """The ask tool's answer (POST /ask's); on_event, where given, has each pipeline event as it is sent."""
        arguments = {"question": question, "purpose": purpose, "arm": arm, "products": products,
                     "history": [as_json(h) for h in history or ()], "runtimes": runtimes}
        if stages is not None:
            arguments["stages"] = as_json(stages)
        return self.call(ANSWER_MOUNT, "ask", arguments, on_event)

    def call(self, mount: str, name: str, arguments: dict, on_event: Callable[[dict], None] | None = None) -> dict:
        """One tool call on mount, its structured result; a refusal or a server that does not answer as the module
        says."""
        async def call() -> dict:
            return await self.session_call(mount, name, arguments, on_event)
        try:
            if self.loop is not None:
                return anyio.from_thread.run(call, token=self.loop)
            return anyio.run(call)
        except BaseException as exc:
            raise typed(exc) from None

    async def session_call(self, mount: str, name: str, arguments: dict, on_event) -> dict:
        """The tool's result on a session of its own. The call is sent as ClientSession.call_tool sends it, but for
        the client's check of the result against the tool's output schema: the server built the result from that
        same model, and the check costs a tools/list call and a JSON Schema compile on every session, which is one
        a call."""
        from mcp import ClientSession, types
        from mcp.client.streamable_http import streamable_http_client

        from kge.adapters.http import async_http_client
        relay = Relay(on_event) if on_event is not None else None
        headers = {"x-kge-authorization": f"Bearer {self.bearer}"}
        async with anyio.create_task_group() as tasks:
            if relay is not None:
                relay.stop = tasks.cancel_scope
            async with async_http_client(app=self.app, tasks=tasks, headers=headers, timeout=self.timeout) as http:
                async with streamable_http_client(f"{self.url}{mount}", http_client=http) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.send_request(
                            types.ClientRequest(types.CallToolRequest(params=types.CallToolRequestParams(name=name, arguments=arguments))),
                            types.CallToolResult, progress_callback=relay)
            tasks.cancel_scope.cancel()
        if relay is not None and relay.error is not None:
            raise relay.error
        if result.isError:
            text = next((c.text for c in result.content if getattr(c, "type", None) == "text"), "")
            matched = REFUSAL.match(text)
            raise refused(int(matched.group(1)), matched.group(2)) if matched else refused(None, text)
        return result.structuredContent


class Relay:
    """The progress callback that hands each event to on_event: once, in the order the server counted them, an event
    whose count is not past the last one's (a repeat) dropped. The first error on_event raises (its reader gone, say)
    stops the call (stop, the call's cancel scope) and is the call's error, where the SDK would log it and go on."""

    def __init__(self, on_event: Callable[[dict], None]):
        self.on_event = on_event
        self.last = 0.0
        self.error: Exception | None = None
        self.stop = None

    async def __call__(self, progress: float, total: float | None, message: str | None) -> None:
        if self.error is not None or progress <= self.last or message is None:
            return
        self.last = progress
        try:
            self.on_event(json.loads(message))
        except Exception as exc:
            self.error = exc
            if self.stop is not None:
                self.stop.cancel()


def typed(exc: BaseException) -> BaseException:
    """The error a failed call raises: a refusal or an ExternalCallError as it was raised, an HTTP refusal of the
    mount itself (no bearer, a bad one) as the typed refusal of its status, a server that did not answer as an
    ExternalCallError; found inside the exception groups the SDK's task groups wrap it in."""
    leaves = list(flatten(exc))
    for leaf in leaves:
        if isinstance(leaf, (ToolRefused, ExternalCallError)):
            return leaf
    for leaf in leaves:
        if isinstance(leaf, httpx.HTTPStatusError):
            return refused(leaf.response.status_code, said(leaf.response))
    for leaf in leaves:
        if isinstance(leaf, (httpx.HTTPError, OSError, anyio.ClosedResourceError, anyio.EndOfStream)):
            return ExternalCallError(f"KGE's tools did not answer ({type(leaf).__name__})", transient=True)
    return exc


def said(response) -> str:
    """A refused response's detail, as the API words it, where its body was read; else its reason phrase."""
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError, httpx.ResponseNotRead):
        detail = None
    return str(detail or response.reason_phrase)


def flatten(exc: BaseException):
    if isinstance(exc, BaseExceptionGroup):
        for inner in exc.exceptions:
            yield from flatten(inner)
    else:
        yield exc
