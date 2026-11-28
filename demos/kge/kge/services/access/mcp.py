"""Bearer-only access tools with the HTTP administrator dependency."""
import anyio
from fastapi import HTTPException
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from kge.services.access import routes
from kge.services.access.models import AccessResponse, AuditEntry, AuditResponse, GrantBody, Limit
from kge.services.http import admin, mcp_server as capability_server, mcp_subject

SERVICE_FUNCTIONS = routes.SERVICE_FUNCTIONS


async def invoke(ctx, operation):
    def run():
        try:
            return operation(admin(mcp_subject(ctx.request_context.request)))
        except HTTPException as exc:
            raise ToolError(f"{exc.status_code}: {exc.detail}") from None
    return await anyio.to_thread.run_sync(run)


def mcp_server() -> FastMCP:
    server = capability_server("access", {name: admin for name in SERVICE_FUNCTIONS})
    tags = ["internal: governance"]

    @server.tool(structured_output=True, meta={"route": "GET /access", "tags": tags, "role": "access_admin"})
    async def list_access(ctx: Context) -> AccessResponse:
        """Internal: list people, administrators, served roles and outside changes."""
        return await invoke(ctx, routes.list_access)

    @server.tool(structured_output=True, meta={"route": "PUT /access/{email}", "tags": tags, "role": "access_admin"})
    async def grant_access(email: str, roles: list[str], ctx: Context, name: str = "") -> AuditEntry:
        """Internal: replace ordinary roles; never grant admin."""
        return await invoke(ctx, lambda who: routes.grant_access(email, GrantBody(roles=roles, name=name), who))

    @server.tool(structured_output=True, meta={"route": "DELETE /access/{email}", "tags": tags, "role": "access_admin"})
    async def revoke_access(email: str, ctx: Context) -> AuditEntry:
        """Internal: remove ordinary roles, keeping operator-set administrator membership."""
        return await invoke(ctx, lambda who: routes.revoke_access(email, who))

    @server.tool(structured_output=True, meta={"route": "GET /access/audit", "tags": tags, "role": "access_admin"})
    async def access_audit(ctx: Context, limit: Limit = 200) -> AuditResponse:
        """Internal: read the newest access changes and report outside changes."""
        return await invoke(ctx, lambda who: routes.access_audit(limit, who))

    return server
