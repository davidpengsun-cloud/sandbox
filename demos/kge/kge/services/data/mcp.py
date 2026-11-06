import anyio
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from kge.services.data import service
from kge.services.data.models import ExportResponse, RowsResponse
from kge.services.http import may, mcp_principal, mcp_server as capability_server, served


def mcp_server() -> FastMCP:
    server = capability_server("data", dict.fromkeys(("preview_data", "export_data", "query_data"),
                                                     lambda who: may(who, "querying")))

    async def invoke(ctx: Context, call):
        def run():
            try:
                who = may(mcp_principal(ctx.request_context.request), "querying")
                return call(who)
            except service.DataRefused as exc:
                raise ToolError(f"{exc.status}: {exc.detail}") from None
            except Exception as exc:
                if hasattr(exc, "status_code") and hasattr(exc, "detail"):
                    raise ToolError(f"{exc.status_code}: {exc.detail}") from None
                raise
        return await anyio.to_thread.run_sync(run)

    @server.tool(name="preview_data", structured_output=True, meta={"route": "POST /data/preview", "tags": ["external: query"], "role": "querying"})
    async def preview_data(unit: str, table: str, ctx: Context, limit: int = 20) -> RowsResponse:
        """Preview the first rows of a governed product. As POST /data/preview."""
        return await invoke(ctx, lambda who: service.preview(served.engine(), who, unit, table, limit))

    @server.tool(name="export_data", structured_output=True, meta={"route": "POST /data/export", "tags": ["external: query"], "role": "querying"})
    async def export_data(unit: str, table: str, ctx: Context) -> ExportResponse:
        """Return rows for a capped governed product export. As POST /data/export."""
        return await invoke(ctx, lambda who: service.export(served.engine(), who, unit, table))

    @server.tool(name="query_data", structured_output=True, meta={"route": "POST /data/query", "tags": ["external: query"], "role": "querying"})
    async def query_data(unit: str, sql: str, ctx: Context) -> RowsResponse:
        """Run read-only SQL against one readable unit, with a row cap. As POST /data/query."""
        return await invoke(ctx, lambda who: service.query(served.engine(), who, unit, sql))

    return server
