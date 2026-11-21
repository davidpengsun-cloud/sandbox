"""/mcp: the context service's MCP server, its three tools the ask service's endpoints they name."""
from __future__ import annotations

import json
import threading
from contextvars import ContextVar

import anyio
from anyio.lowlevel import current_token
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from kge.adapters.model import ExternalCallError
from kge.services.http import may, mcp_principal, mcp_server as capability_server, served, tool_matrix
from kge.services.ask import service
from kge.services.ask.models import (Ask, AskResponse, ContextAsk, ContextResponse, Feedback, FeedbackAccepted,
                                     Retrieve, RetrieveResponse, RuntimesResponse, Stages, TermMap)
from kge.semantics.plan import HistoryEntry
from kge.services.ask.routes import as_compact_unless_engineer, ask_feedback, context_package, engine, resolved_map, retrieve, run_config

SERVICE_FUNCTIONS = {"ask": service.ask, "list_runtimes": service.list_runtimes, "feedback": service.feedback}

def mcp_server() -> FastMCP:
    """A fresh MCP server for /mcp, made as the app starts (a session manager runs once): the official mcp SDK over
    streamable HTTP, stateless, answering in JSON. Its tools are the endpoints they name, each run as the principal
    whose bearer token the MCP request carries. The SDK's Host check is off, so it answers on a deployment host; a
    bearer is still required."""
    server = capability_server("context", {"get_context": tool_matrix("ask", "get_context"),
                                           "retrieve": tool_matrix("retrieval", "retrieve"),
                                           "map_term": tool_matrix("retrieval", "map_term")})

    @server.tool(name="get_context", structured_output=True, meta={"route": "POST /context", "tags": ["external: knowledge"]})
    async def get_context(question: str, ctx: Context, purpose: str = "analysis", arm: str = "treatment",
                          products: list[str] | None = None, plan_vocabulary: bool = False,
                          history: list[HistoryEntry] | None = None, compact: bool = False,
                          runtimes: list[str] | None = None) -> ContextResponse:
        """The context package the reference agent's prompt carries for a question, on one arm: control (graph off) or
        treatment (graph on), as the caller may read. compact keeps definitions in the prompt block and omits SQL, traces, plans and duplicate candidates.
        As POST /context."""
        return await tool(ctx, lambda who: context_package(ContextAsk(question=question, purpose=purpose, arm=arm,
                          products=products, plan_vocabulary=plan_vocabulary, history=history or [], compact=compact, runtimes=runtimes), who), querying)

    @server.tool(name="retrieve", structured_output=True, meta={"route": "POST /retrieve", "tags": ["external: knowledge"]})
    async def retrieve_tool(query: str, ctx: Context, purpose: str = "assistant", k: int = 5, shelf: str | None = None,
                            tiers: list[str] | None = None) -> RetrieveResponse:
        """Vector search over the chunks the caller may read for a purpose; tiers: only AI-ready documents at these tiers.
        As POST /retrieve."""
        return await tool(ctx, lambda who: retrieve(Retrieve(query=query, purpose=purpose, k=k, shelf=shelf, tiers=tiers), who), querying)

    @server.tool(name="map_term", structured_output=True, meta={"route": "GET /map/{term}", "tags": ["external: knowledge"]})
    async def map_term(term: str, ctx: Context) -> TermMap:
        """A term's map: its certified columns, the documents that define it, its contexts, and the no-join facts and
        certified joins among their units, marked as the caller may read them. As GET /map/{term}."""
        return await tool(ctx, lambda who: resolved_map(term, who), querying)

    return server


def answer_mcp_server() -> FastMCP:
    """The answer capability's MCP server at /mcp/answer, with POST /ask's model and querying check. A tools/call that
    asks for progress is answered as an event stream (EventStreamSessions), so its ask sends the pipeline's events as
    it runs; any other request is answered in JSON as before."""
    server = capability_server("answer", {"ask": tool_matrix("ask", "ask"), "list_runtimes": tool_matrix("ask", "list_runtimes"),
                                          "feedback": tool_matrix("feedback", "feedback")})
    EventStreamSessions.adopt(server.session_manager)

    @server.tool(name="ask", structured_output=True, meta={"route": "POST /ask", "tags": ["external: query"], "role": "querying"})
    async def ask(question: str, ctx: Context, purpose: str = "analysis", arm: str = "graph",
                  stages: Stages | None = None, products: list[str] | None = None,
                  history: list[HistoryEntry] | None = None, compact: bool = False,
                  runtimes: list[str] | None = None, display: bool = False) -> AskResponse:
        """Answer a business question with governed figures. Show answer_text to the person as given; it starts with the
        label: Certified where the release's rules answered it, or AI-Generated where the model gave its best guess. A
        question neither can answer is not sure, and says what is missing. Whether an AI-Generated answer may be given is
        the deployment's ASK_AI_FALLBACK, on or off, named in the reply's ai_fallback. Never compute or change a figure.
        compact omits SQL, traces and duplicate plans, preserving runtime labels and follow-up history. display adds
        the rich answer's display block, the tool's counterpart of POST /ask's display query flag; unlike that flag,
        which gives a compact answer no block, a compact answer here gets its card's part of the block (headline, table,
        chart and notes) without the queries and their checks. As POST /ask."""
        body = Ask(question=question, purpose=purpose, arm=arm, stages=stages, products=products, history=history or [],
                   compact=compact, runtimes=runtimes)
        cfg = run_config(body, "ask")
        events = EventRelay(ctx)

        def answer(who: str) -> dict:
            run = as_compact_unless_engineer(body, who)
            if isinstance(cfg, JSONResponse):
                return cfg
            return events.relayed(run.question, run.compact, lambda on_stage=None: service.ask(
                served.combined(), served.PROFILE, run.question, who, run.purpose, run.arm, cfg, products=run.products,
                history=run.history, compact=run.compact, runtimes=run.runtimes, on_stage=on_stage,
                **({"display_engine": engine} if display else {})))
        return await tool(ctx, answer, querying)

    @server.tool(name="feedback", structured_output=True, meta={"route": "POST /ask/feedback", "tags": ["external: query"], "role": "querying"})
    async def feedback(receipt: str, verdict: str, ctx: Context) -> FeedbackAccepted:
        """Record whether an answer was what the person meant: verdict confirmed or not_what_i_meant, with the answer's
        receipt as given. Call it only when the person says so."""
        return await tool(ctx, lambda who: ask_feedback(Feedback(receipt=receipt, verdict=verdict), who), querying)

    @server.tool(name="list_runtimes", structured_output=True, meta={"route": "GET /runtimes", "tags": ["external: query"], "role": "querying"})
    async def list_runtimes(ctx: Context) -> RuntimesResponse:
        """The runtimes the caller may use, and which reference tables each holds. As GET /runtimes."""
        return await tool(ctx, lambda who: service.list_runtimes(served.engine(), who), querying)

    return server


def querying(who: str) -> str:
    return may(who, "querying")


async def tool(ctx: Context, call, check=querying) -> dict:
    """call(who) off the event loop, who the querying principal whose bearer token the MCP request carries. What the endpoint
    would refuse is the tool's error, with its status: 401 or 403 as the API refuses, a 400 in its own words."""
    request = ctx.request_context.request

    def run() -> dict:
        try:
            out = call(check(mcp_principal(request)))
        except HTTPException as exc:
            raise ToolError(f"{exc.status_code}: {exc.detail}") from None
        except ExternalCallError as exc:
            raise ToolError(f"503: {exc}") from None
        if isinstance(out, JSONResponse):
            raise ToolError(f"{out.status_code}: {json.loads(out.body)['error']}")
        return out
    return await anyio.to_thread.run_sync(run)


class EventRelay:
    """The ask tool's pipeline events, for a call that asked for progress (its progressToken): each event the direct
    stream would send the request (service.public_event: a compact request's as compact), but the answer, which is the
    tool's result, sent as a progress notification on the request's own stream, its message the event's JSON and its
    progress the event's count, in the order the pipeline sent them. The notification is sent on the server's event
    loop (loop) from whichever thread the pipeline emits on. A call that did not ask is answered as before."""

    def __init__(self, ctx: Context):
        meta = getattr(getattr(ctx, "request_context", None), "meta", None)
        self.ctx = ctx
        self.wanted = getattr(meta, "progressToken", None) is not None
        self.loop = current_token() if self.wanted else None
        self.sent = 0
        self.lock = threading.Lock()

    def relayed(self, question: str, compact: bool, compute) -> dict:
        """compute(on_stage)'s answer, its events sent as they come where the call asked for them."""
        if not self.wanted:
            return compute()

        def emit(event: dict) -> None:
            if (event := service.public_event(event, compact)) is None or event.get("event") == "answer":
                return
            with self.lock:
                self.sent += 1
                anyio.from_thread.run(self.ctx.report_progress, self.sent, None,
                                      json.dumps(event, ensure_ascii=False, default=str), token=self.loop)
        with service.recording(question, emit) as recorder:
            out = compute(recorder.on_stage)
        if not isinstance(out, JSONResponse):
            recorder.emit_final(out)
        return out


EVENT_STREAM: ContextVar[bool] = ContextVar("mcp_event_stream", default=False)


class EventStreamSessions(StreamableHTTPSessionManager):
    """A stateless JSON session manager that answers a request as an event stream while EVENT_STREAM is set for it
    (MCPEndpoint sets it for a tools/call that asks for progress), so the call's notifications reach the client: a
    JSON reply carries none."""

    @property
    def json_response(self) -> bool:
        return self._json_response and not EVENT_STREAM.get()

    @json_response.setter
    def json_response(self, value: bool) -> None:
        self._json_response = value

    @classmethod
    def adopt(cls, sessions: StreamableHTTPSessionManager) -> None:
        """Make the server's own session manager, as FastMCP built it, one of these."""
        json_response = vars(sessions).pop("json_response")
        sessions.__class__ = cls
        sessions.json_response = json_response


def asks_progress(body: bytes | None) -> bool:
    """Whether body is one tools/call request with a progressToken."""
    try:
        message = json.loads(body) if body else None
    except ValueError:
        return False
    params = message.get("params") if isinstance(message, dict) and message.get("method") == "tools/call" else None
    meta = params.get("_meta") if isinstance(params, dict) else None
    return isinstance(meta, dict) and meta.get("progressToken") is not None


async def buffered(receive, limit: int):
    """(the request's body, or None where it is longer than limit, and a receive that gives the same messages again)."""
    messages, size = [], 0
    while True:
        message = await receive()
        messages.append(message)
        if message["type"] != "http.request":
            break
        size += len(message.get("body", b""))
        if not message.get("more_body") or size > limit:
            break
    whole = messages[-1]["type"] == "http.request" and not messages[-1].get("more_body") and size <= limit
    pending = list(messages)

    async def replay():
        return pending.pop(0) if pending else await receive()
    return (b"".join(m.get("body", b"") for m in messages) if whole else None), replay


class MCPEndpoint:
    """/mcp, an ASGI app in the API's routes: a request with no bearer token, or a bad one, is refused there, a 401 as the
    API's (raised to the app's handler before any response starts); any other goes to the MCP server's session manager.
    Where the server streams events (EventStreamSessions), a POST's body is read first to see whether it asks for
    progress."""

    def __init__(self, server: FastMCP):
        self.server = server

    async def __call__(self, scope, receive, send) -> None:
        mcp_principal(Request(scope))
        sessions = self.server.session_manager
        if not isinstance(sessions, EventStreamSessions) or scope.get("method") != "POST":
            await sessions.handle_request(scope, receive, send)
            return
        body, receive = await buffered(receive, sessions.max_request_body_size)
        streaming = EVENT_STREAM.set(asks_progress(body))
        try:
            await sessions.handle_request(scope, receive, send)
        finally:
            EVENT_STREAM.reset(streaming)
