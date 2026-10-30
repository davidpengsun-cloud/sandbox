"""MCP adapter for the same search function, identity checks and models as HTTP."""
from mcp.server.fastmcp import Context, FastMCP

from kge.services.http import mcp_server as capability_server, served
from kge.services.search import service
from kge.services.search.models import Kind, Limit, Query, Search, SearchResponse
from kge.services.search.routes import querying


def mcp_server(invoke) -> FastMCP:
    server = capability_server("search", {"search": querying})

    @server.tool(name="search", structured_output=True,
                 meta={"route": "POST /search", "tags": ["external: knowledge"]})
    async def search(query: Query, ctx: Context, kinds: list[Kind] | None = None, limit: Limit = 20) -> SearchResponse:
        """Ranked knowledge hits within the caller's readable units. As POST /search."""
        body = Search(query=query, kinds=kinds, limit=limit)
        return await invoke(ctx, lambda who: service.search(served.engine(), who, body), querying)

    return server
