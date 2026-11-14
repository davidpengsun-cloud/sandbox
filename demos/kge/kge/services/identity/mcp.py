"""The signed-in person's readable units over MCP."""
import anyio
from mcp.server.fastmcp import Context, FastMCP

from kge.services.http import mcp_subject, mcp_server as capability_server
from kge.services.identity.models import ReadableUnits
from kge.services.identity.routes import readable_units_for


def mcp_server() -> FastMCP:
    server = capability_server("identity")

    @server.tool(name="readable_units", structured_output=True, meta={"route": "GET /readable-units", "tags": ["external: identity"]})
    async def readable_units(ctx: Context) -> ReadableUnits:
        """The unit ids the signed-in person may read. As GET /readable-units."""
        def call() -> ReadableUnits:
            return readable_units_for(mcp_subject(ctx.request_context.request))
        return await anyio.to_thread.run_sync(call)

    return server
