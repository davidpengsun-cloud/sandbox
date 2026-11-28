"""MCP adapter for the graph read and explore: the same service functions, check, models and errors as HTTP."""
from typing import Annotated

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

from kge.services.graph import routes, service
from kge.services.graph.models import Explore, GraphPage
from kge.services.http import mcp_server as capability_server

NodeRef = Annotated[str, Field(min_length=1, max_length=2000)]
Cursor = Annotated[str, Field(min_length=1, max_length=4000)]
Limit = Annotated[int, Field(ge=1, le=service.MAX_LIMIT)]


def mcp_server(invoke) -> FastMCP:
    server = capability_server("graph", {name: routes.tool_check(name) for name in ("read_graph", "explore", "explore_question")})

    @server.tool(name="read_graph", structured_output=True,
                 meta={"route": "GET /graph", "tags": ["external: knowledge"]})
    async def read_graph(ctx: Context, node: NodeRef | None = None, cursor: Cursor | None = None,
                         limit: Limit = service.DEFAULT_LIMIT) -> GraphPage:
        """The graph's nodes and links the caller may read, each link certified, proposed or inferred with the reason it
        exists; no figures. Paged by a cursor; with node, that node's neighbourhood. As GET /graph."""
        return await invoke(ctx, lambda who: routes.graph_page(who, node, cursor, limit), routes.querying)

    @server.tool(name="explore", structured_output=True,
                 meta={"route": "GET /graph/explore", "tags": ["external: knowledge"]})
    async def explore(ctx: Context, node: NodeRef) -> Explore:
        """A short summary of a node's neighbourhood the caller may read, with no figures, and a deep link that opens the
        graph on the node; a hierarchy term's members and codes, a join's (join:<id>) facts. As GET /graph/explore."""
        return await invoke(ctx, lambda who: routes.explore_node(who, node), routes.querying)

    @server.tool(name="explore_question", structured_output=True,
                 meta={"route": "POST /graph/explore", "tags": ["external: knowledge"]})
    async def explore_question(ctx: Context, question: NodeRef) -> Explore:
        """A short summary of a question's neighbourhood the caller may read, with no figures, a deep link that opens the
        graph on the node the question names best, and the question's whole path in order: term, context, fact and
        measure, each join hop's kind, cardinality, type and validity window, the dimensions, filters, exclusions and
        time rules. As POST /graph/explore."""
        return await invoke(ctx, lambda who: routes.explore_question(who, question), routes.querying)

    return server
