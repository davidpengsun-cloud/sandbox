"""The same assistance over MCP, with the same caller and budget."""
from mcp.server.fastmcp import Context, FastMCP

from kge.services.assist import service
from kge.services.assist.models import Assist, AssistResponse, Stages
from kge.services.assist.routes import invoke, querying
from kge.services.http import mcp_server as capability_server

SERVICE_FUNCTIONS = {"assist": service.assist}


def mcp_server(invoke_tool) -> FastMCP:
    server = capability_server("assist", {"assist": querying})

    @server.tool(name="assist", structured_output=True,
                 meta={"route": "POST /assist", "tags": ["external: query"], "role": "querying"})
    async def assist(question: str, ctx: Context, purpose: str = "analysis", arm: str = "graph",
                     stages: Stages | None = None, products: list[str] | None = None) -> AssistResponse:
        """Read-only answers from the catalogue, Search or governed Ask. As POST /assist."""
        body = Assist(question=question, purpose=purpose, arm=arm, stages=stages, products=products)
        return await invoke_tool(ctx, lambda who: invoke(body, who), querying)

    return server
