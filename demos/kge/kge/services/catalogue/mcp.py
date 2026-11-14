import anyio
from mcp.server.fastmcp import Context, FastMCP

from kge.services.catalogue.models import CatalogueResponse
from kge.services.catalogue.routes import catalogue_for
from kge.services.http import mcp_subject, mcp_server as capability_server


def mcp_server() -> FastMCP:
    server = capability_server("catalogue")

    @server.tool(name="get_catalogue", structured_output=True, meta={"route": "GET /catalogue", "tags": ["external: knowledge"]})
    async def get_catalogue(ctx: Context) -> CatalogueResponse:
        """The governed products the signed-in person may discover. As GET /catalogue."""
        return await anyio.to_thread.run_sync(lambda: catalogue_for(mcp_subject(ctx.request_context.request)))

    return server
