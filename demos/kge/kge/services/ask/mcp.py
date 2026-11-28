"""/mcp: the context service's MCP server, its three tools the ask service's endpoints they name."""
from __future__ import annotations

import json
import threading
from contextvars import ContextVar
from typing import Annotated

import anyio
from anyio.lowlevel import current_token
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from pydantic import Field

from kge.adapters.model import ExternalCallError
from kge.services.http import AGENT_INSTRUCTIONS, may, mcp_principal, mcp_server as capability_server, served, tool_matrix
from kge.services.ask import service
from kge.services import telemetry
from kge.services.telemetry import Agents, ModelUsage, Overview, Sessions, TraceDetail, Traces
from kge.ports import StoreError
from kge.pipeline import questionrun
from kge.services.ask.models import (Ask, AskToolResponse, ContextAsk, ContextResponse, Feedback, FeedbackAccepted,
                                     QuestionRunResponse, Retrieve, RetrieveResponse, RuntimesResponse, Stages, TermMap)
from kge.semantics.plan import HistoryEntry
from kge.services.ask.routes import (as_compact_unless_engineer, ask_feedback, context_package, engine, followed_run, held_run,
                                     resolved_map, retrieve, run_config, stopped_run)

SERVICE_FUNCTIONS = {"ask": service.ask, "list_runtimes": service.list_runtimes, "feedback": service.feedback,
                     "question_run": service.get_run, "stop_question_run": service.stop_run}

def telemetry_call(function, *args):
    """The telemetry transport's errors, as MCP tool errors through the shared wrapper."""
    try:
        return function(served.PROFILE, *args)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except (StoreError, OSError) as exc:
        raise HTTPException(503, f"telemetry is unavailable: {type(exc).__name__}") from None


def retrieve_check(who: str, arguments: dict | None = None) -> str:
    """Legacy vector retrieval remains engineer-only; citable passage mode permits querying roles."""
    row = "ask" if arguments and arguments.get("include_passages") is True else "retrieval"
    return tool_matrix(row, "retrieve")(who)


retrieve_check.accepts_arguments = True


def mcp_server() -> FastMCP:
    """A fresh MCP server for /mcp, made as the app starts (a session manager runs once): the official mcp SDK over
    streamable HTTP, stateless, answering in JSON. Its tools are the endpoints they name, each run as the principal
    whose bearer token the MCP request carries. The SDK's Host check is off, so it answers on a deployment host; a
    bearer is still required."""
    server = capability_server("context", {"get_context": tool_matrix("ask", "get_context"),
                                           "retrieve": retrieve_check,
                                           "map_term": tool_matrix("ask", "map_term"),
                                           "telemetry_agents": tool_matrix("telemetry", "telemetry_agents"),
                                           "telemetry_sessions": tool_matrix("telemetry", "telemetry_sessions"),
                                           "telemetry_traces": tool_matrix("telemetry", "telemetry_traces"),
                                           "telemetry_trace": tool_matrix("telemetry", "telemetry_trace"),
                                           "telemetry_usage": tool_matrix("telemetry", "telemetry_usage"),
                                           "telemetry_overview": tool_matrix("telemetry", "telemetry_overview")},
                               instructions=AGENT_INSTRUCTIONS)

    @server.tool(name="get_context", structured_output=True, meta={"route": "POST /context", "tags": ["external: knowledge"]})
    async def get_context(question: str, ctx: Context, purpose: str = "analysis", arm: str = "treatment",
                          products: list[str] | None = None, plan_vocabulary: bool = False,
                          history: list[HistoryEntry] | None = None, compact: bool = False,
                          runtimes: list[str] | None = None, open_items: bool = False) -> ContextResponse:
        """The context package the reference agent's prompt carries for a question, on one arm: control (graph off) or
        treatment (graph on), as the caller may read. compact keeps definitions in the prompt block and omits SQL, traces, plans and duplicate candidates.
        Lists a question's open items. As POST /context."""
        return await tool(ctx, lambda who: context_package(ContextAsk(question=question, purpose=purpose, arm=arm,
                          products=products, plan_vocabulary=plan_vocabulary, history=history or [], compact=compact, runtimes=runtimes,
                          open_items=open_items), who), querying)

    @server.tool(name="retrieve", structured_output=True, meta={"route": "POST /retrieve", "tags": ["external: knowledge"]})
    async def retrieve_tool(query: str, ctx: Context, purpose: str = "assistant", k: int = 5, shelf: str | None = None,
                            tiers: list[str] | None = None, include_passages: bool = False) -> RetrieveResponse:
        """Vector search over the chunks the caller may read for a purpose; tiers: only AI-ready documents at these tiers.
        As POST /retrieve."""
        return await tool(ctx, lambda who: retrieve(Retrieve(query=query, purpose=purpose, k=k, shelf=shelf, tiers=tiers, include_passages=include_passages), who=who), querying)

    @server.tool(name="map_term", structured_output=True, meta={"route": "GET /map/{term}", "tags": ["external: knowledge"]})
    async def map_term(term: str, ctx: Context) -> TermMap:
        """A term's map: its definition and steward, the data sets that hold it, the documents that define it and its
        contexts and each context's explained path (fact, measure, join hops, dimensions, conditions, time rules), as
        the caller may read them; for an engineer also its certified columns and the no-join facts and certified joins
        among their units, for anyone else business names alone. As GET /map/{term}."""
        return await tool(ctx, lambda who: resolved_map(term, who), querying)

    @server.tool(name="telemetry_agents", structured_output=True, meta={"route": "GET /telemetry/agents", "tags": ["internal: governance"], "role": "querying"})
    async def telemetry_agents(ctx: Context, start: str | None = None, end: str | None = None) -> Agents:
        """List agents with retained question traces."""
        return await tool(ctx, lambda who: telemetry_call(telemetry.agents, start, end))

    @server.tool(name="telemetry_sessions", structured_output=True, meta={"route": "GET /telemetry/sessions", "tags": ["internal: governance"], "role": "querying"})
    async def telemetry_sessions(ctx: Context, agent: str | None = None, start: str | None = None, end: str | None = None) -> Sessions:
        """List opaque sessions for an agent and period."""
        return await tool(ctx, lambda who: telemetry_call(telemetry.sessions, agent, start, end))

    @server.tool(name="telemetry_traces", structured_output=True, meta={"route": "GET /telemetry/traces", "tags": ["internal: governance"], "role": "querying"})
    async def telemetry_traces(ctx: Context, agent: str | None = None, session: str | None = None, start: str | None = None, end: str | None = None, outcome: str | None = None, day: str | None = None, day_agent: str | None = None, limit: int = 50, cursor: str | None = None) -> Traces:
        """List trace summaries for a session and period."""
        return await tool(ctx, lambda who: telemetry_call(telemetry.traces, agent, session, start, end, outcome, day, day_agent, limit, cursor))

    @server.tool(name="telemetry_overview", structured_output=True, meta={"route": "GET /telemetry/overview", "tags": ["internal: governance"], "role": "querying"})
    async def telemetry_overview(ctx: Context, start: str | None = None, end: str | None = None, agent: str | None = None, session: str | None = None, outcome: str | None = None) -> Overview:
        """Read aggregate question telemetry for a period."""
        return await tool(ctx, lambda who: telemetry_call(telemetry.overview, start, end, agent, session, outcome))

    @server.tool(name="telemetry_trace", structured_output=True, meta={"route": "GET /telemetry/traces/{trace_id}", "tags": ["internal: governance"], "role": "querying"})
    async def telemetry_trace(trace_id: str, ctx: Context, start: str | None = None, end: str | None = None) -> TraceDetail:
        """Read one retained trace with safe spans."""
        return await tool(ctx, lambda who: telemetry_call(telemetry.trace, trace_id, start, end))

    @server.tool(name="telemetry_usage", structured_output=True, meta={"route": "GET /telemetry/usage", "tags": ["internal: governance"], "role": "querying"})
    async def telemetry_usage(ctx: Context, start: str | None = None, end: str | None = None, agent: str | None = None) -> ModelUsage:
        """Read daily model calls and known usage by agent and model."""
        return await tool(ctx, lambda who: telemetry_call(telemetry.usage, start, end, agent))

    return server


def answer_mcp_server() -> FastMCP:
    """The answer capability's MCP server at /mcp/answer, with POST /ask's model and querying check. A tools/call that
    asks for progress is answered as an event stream (EventStreamSessions), so its ask sends the pipeline's events as
    it runs; any other request is answered in JSON as before."""
    server = capability_server("answer", {"ask": tool_matrix("ask", "ask"), "list_runtimes": tool_matrix("ask", "list_runtimes"),
                                          "feedback": tool_matrix("feedback", "feedback"),
                                          "question_run": tool_matrix("ask", "question_run"),
                                          "stop_question_run": tool_matrix("ask", "stop_question_run")},
                               instructions=AGENT_INSTRUCTIONS)
    EventStreamSessions.adopt(server.session_manager)

    @server.tool(name="ask", structured_output=True, meta={"route": "POST /ask", "tags": ["external: query"], "role": "querying"})
    async def ask(question: str, ctx: Context, purpose: str = "analysis", arm: str = "graph",
                  stages: Stages | None = None, products: list[str] | None = None,
                  history: list[HistoryEntry] | None = None, compact: bool = False,
                  runtimes: list[str] | None = None, display: bool = False, background: bool = False) -> AskToolResponse:
        """Answer a business question with governed figures. Show answer_text to the person as given; it starts with the
        label: Certified where the release's rules answered it, or AI-Generated where the model gave its best guess. A
        question neither can answer is not sure, and says what is missing. Whether an AI-Generated answer may be given is
        the deployment's ASK_AI_FALLBACK, on or off, named in the reply's ai_fallback. Never compute or change a figure.
        compact keeps the answered query's SQL and sql_certified (false unless the answer is certified) and omits plans,
        traces and duplicate plans, preserving runtime labels and follow-up history: plan and trace stay the engineer's
        (the role matrix's row 7). display adds
        the rich answer's display block, the tool's counterpart of POST /ask's display query flag; unlike that flag,
        which gives a compact answer no block, a compact answer here gets its card's part of the block (headline, table,
        chart and notes) without the queries and their checks. background, the counterpart of POST /ask's background
        query flag, runs the question in KGE's durable question store: an answer ready within QUESTION_START_WAIT is
        given as without it; otherwise the reply is {"run": {id, state, expires_at, poll_after, events, cursor}}, to
        follow with question_run every poll_after seconds, or stop with stop_question_run. Check get_context's open items
        first. As POST /ask."""
        body = Ask(question=question, purpose=purpose, arm=arm, stages=stages, products=products, history=history or [],
                   compact=compact, runtimes=runtimes)
        cfg = run_config(body, "ask")
        events = EventRelay(ctx)

        def answer(who: str) -> dict:
            run = as_compact_unless_engineer(body, who)
            if isinstance(cfg, JSONResponse):
                return cfg
            if background:
                wait = questionrun.start_wait()
                return service.background_reply(held_run(lambda: service.start_run(
                    served.combined(), served.PROFILE, run, who, cfg, presentation=display, card=display, wait=wait,
                    on_event=events.run_event if events.wanted else None)), engine)
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

    @server.tool(name="question_run", structured_output=True, meta={"route": "GET /ask/runs/{run_id}", "tags": ["external: query"], "role": "querying"})
    async def question_run(run_id: str, ctx: Context, after: Annotated[int, Field(ge=0)] = 0) -> QuestionRunResponse:
        """Follow a background question the ask tool started (its run's id): its state (running, done, failed or
        expired), the progress events after the cursor `after` (0 for all), the cursor to pass next, poll_after (the
        seconds to wait before the next call) and expires_at; its answer when done, as the ask tool would have given it;
        error and reason (warehouse_time_limit, run_time_limit, warehouse_failure, stopped or access_changed) when it ended without one.
        Only the person who asked can follow it. As GET /ask/runs/{run_id}."""
        return await tool(ctx, lambda who: service.run_reply(followed_run(run_id, who, after), engine)[0], querying)

    @server.tool(name="stop_question_run", structured_output=True, meta={"route": "DELETE /ask/runs/{run_id}", "tags": ["external: query"], "role": "querying"})
    async def stop_question_run(run_id: str, ctx: Context) -> QuestionRunResponse:
        """Stop a background question the ask tool started, when the person asks to: its warehouse work is cancelled and
        the run ends failed, reason stopped. A run that has already ended is given unchanged. Only the person who asked
        can stop it. As DELETE /ask/runs/{run_id}."""
        return await tool(ctx, lambda who: service.run_reply(stopped_run(run_id, who), engine)[0], querying)

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

    def run_event(self, cursor: int, event: dict) -> None:
        """A background run's event, as its run records it (already as the request sees it), sent as a progress
        notification while the call waits for the run's answer; the answer itself is the tool's result."""
        if event.get("event") == "answer":
            return
        with self.lock:
            self.sent += 1
            anyio.from_thread.run(self.ctx.report_progress, self.sent, None,
                                  json.dumps(event, ensure_ascii=False, default=str), token=self.loop)

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
