"""MCP adapter for the same search function, identity checks and models as HTTP."""
from mcp.server.fastmcp import Context, FastMCP

from kge.services.http import AGENT_INSTRUCTIONS, engineer_view, mcp_server as capability_server, served
from kge.services.search import service
from kge.services.search.models import Kind, Limit, Query, Search, SearchResponse
from kge.services.search.routes import querying, tool_check


def mcp_server(invoke) -> FastMCP:
    server = capability_server("search", {"search": tool_check}, instructions=AGENT_INSTRUCTIONS)

    @server.tool(name="search", structured_output=True,
                 meta={"route": "POST /search", "tags": ["external: knowledge"]})
    async def search(query: Query, ctx: Context, kinds: list[Kind] | None = None, limit: Limit = 20) -> SearchResponse:
        """Ranked knowledge hits within the caller's readable units. As POST /search."""
        body = Search(query=query, kinds=kinds, limit=limit)
        return await invoke(ctx, lambda who: hits(service.search(served.engine(), who, body, engineer=engineer_view(who))), querying)

    return server


def hits(response: SearchResponse) -> dict:
    """The response as POST /search sends it, a filter phrase's own fields only on a filter hit: the tool's text content
    is this dict's JSON, so a search without the filter kind reads as develop's did (#707 decision 12)."""
    return response.model_dump(mode="json")
