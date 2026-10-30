"""/mcp: the context service's MCP server, its three tools the ask service's endpoints they name."""
from __future__ import annotations

import json
import anyio
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from kge.adapters.model import ExternalCallError
from kge.services.http import may, mcp_principal, mcp_server as capability_server, served
from kge.services.ask import service
from kge.services.ask.models import (AgentAsk, Ask, AskResponse, ContextResponse, Retrieve, RetrieveResponse, Stages,
                                     TermMap)
from kge.services.ask.routes import context_package, resolved_map, retrieve, run_config

SERVICE_FUNCTIONS = {"ask": service.ask}

def mcp_server() -> FastMCP:
    """A fresh MCP server for /mcp, made as the app starts (a session manager runs once): the official mcp SDK over
    streamable HTTP, stateless, answering in JSON. Its tools are the endpoints they name, each run as the principal
    whose bearer token the MCP request carries. The SDK's Host check is off, so it answers on a deployment host; a
    bearer is still required."""
    server = capability_server("context", dict.fromkeys(("get_context", "retrieve", "map_term"), querying))

    @server.tool(name="get_context", structured_output=True, meta={"route": "POST /context", "tags": ["external: knowledge"]})
    async def get_context(question: str, ctx: Context, purpose: str = "analysis", arm: str = "treatment") -> ContextResponse:
        """The context package the reference agent's prompt carries for a question, on one arm: control (graph off) or
        treatment (graph on), as the caller may read. As POST /context."""
        return await tool(ctx, lambda who: context_package(AgentAsk(question=question, purpose=purpose, arm=arm), who), querying)

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
    """The answer capability's MCP server at /mcp/answer, with POST /ask's model and querying check."""
    server = capability_server("answer", {"ask": querying})

    @server.tool(name="ask", structured_output=True, meta={"route": "POST /ask", "tags": ["external: query"], "role": "querying"})
    async def ask(question: str, ctx: Context, purpose: str = "analysis", arm: str = "graph",
                  stages: Stages | None = None) -> AskResponse:
        """The governed answer, its figures, citations and readable answer_text. As POST /ask."""
        body = Ask(question=question, purpose=purpose, arm=arm, stages=stages)
        cfg = run_config(body, "ask")
        return await tool(ctx, lambda who: cfg if isinstance(cfg, JSONResponse) else service.ask(
            served.combined(), served.PROFILE, body.question, who, body.purpose, body.arm, cfg), querying)

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


class MCPEndpoint:
    """/mcp, an ASGI app in the API's routes: a request with no bearer token, or a bad one, is refused there, a 401 as the
    API's (raised to the app's handler before any response starts); any other goes to the MCP server's session manager."""

    def __init__(self, server: FastMCP):
        self.server = server

    async def __call__(self, scope, receive, send) -> None:
        mcp_principal(Request(scope))
        await self.server.session_manager.handle_request(scope, receive, send)
